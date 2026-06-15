"""
TCP 异步服务器核心
使用 asyncio.start_server 实现高并发连接管理
"""
import asyncio
import time
import base64
import logging

from server.protocol import MessageType, Connection
from server.config import ServerConfig
from server.user_manager import UserManager
from server.group_manager import GroupManager
from server.message_history import MessageHistory
from server.file_transfer import FileTransfer
from server.message_router import MessageRouter
from server.heartbeat import HeartbeatMonitor
from server.database import init_db
from server.p2p_helper import P2PHolePunchHelper

logger = logging.getLogger(__name__)


class ConnectionManager:
    """连接管理器
    维护 conn_id -> connection 和 user_id -> conn_id 两套映射，
    支持并发安全的增删改查。
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._connections = {}   # conn_id -> {conn, user_id, last_heartbeat}
        self._user_conn = {}     # user_id -> conn_id
        self._next_id = 0

    async def add(self, conn: Connection, max_connections: int = None):
        """注册新连接，返回分配的唯一 conn_id"""
        async with self._lock:
            if max_connections is not None and len(self._connections) >= max_connections:
                return None
            self._next_id += 1
            conn_id = self._next_id
            self._connections[conn_id] = {
                "conn": conn,
                "user_id": None,
                "last_heartbeat": time.time(),
            }
            return conn_id

    async def bind_user(self, conn_id: int, user_id: int):
        """将连接绑定到已认证用户。如果该用户已有连接，关闭旧连接"""
        old_conn = None
        async with self._lock:
            info = self._connections.get(conn_id)
            if not info:
                return
            old_conn_id = self._user_conn.get(user_id)
            if old_conn_id is not None and old_conn_id != conn_id:
                old_info = self._connections.get(old_conn_id)
                if old_info:
                    old_info["user_id"] = None
                    old_conn = old_info["conn"]
                    del self._connections[old_conn_id]
            info["user_id"] = user_id
            info["last_heartbeat"] = time.time()
            self._user_conn[user_id] = conn_id
        if old_conn:
            try:
                old_conn.close()
                await old_conn.wait_closed()
            except Exception:
                pass

    async def remove(self, conn_id: int):
        """移除连接并清理关联资源"""
        conn = None
        async with self._lock:
            info = self._connections.pop(conn_id, None)
            if info:
                if info["user_id"]:
                    self._user_conn.pop(info["user_id"], None)
                conn = info["conn"]
        if conn:
            try:
                conn.close()
                await conn.wait_closed()
            except Exception:
                pass

    def get_conn(self, conn_id: int):
        """根据 conn_id 获取 Connection 对象"""
        info = self._connections.get(conn_id)
        return info["conn"] if info else None

    def get_user_id(self, conn_id: int):
        """根据 conn_id 获取绑定的 user_id"""
        info = self._connections.get(conn_id)
        return info["user_id"] if info else None

    def get_by_user(self, user_id: int):
        """根据 user_id 获取其 Connection 对象"""
        conn_id = self._user_conn.get(user_id)
        if conn_id:
            info = self._connections.get(conn_id)
            if info:
                return info["conn"]
        return None

    async def send_to_user(self, user_id: int, msg_type, payload, seq=None) -> bool:
        """向指定用户发送消息。返回是否发送成功"""
        conn = self.get_by_user(user_id)
        if conn and not conn.is_closed:
            try:
                await conn.send_message(msg_type, payload, seq)
                return True
            except (ConnectionError, OSError) as e:
                logger.warning("send_to_user(%s) failed: %s", user_id, e)
        return False

    async def broadcast(self, msg_type, payload, exclude_user_id=None):
        """广播消息给所有在线用户，可选排除某个用户"""
        async with self._lock:
            items = list(self._connections.items())
        for conn_id, info in items:
            uid = info["user_id"]
            if uid is not None and uid != exclude_user_id:
                try:
                    await info["conn"].send_message(msg_type, payload)
                except (ConnectionError, OSError):
                    pass

    def update_heartbeat(self, conn_id: int):
        """更新连接的最后心跳时间"""
        info = self._connections.get(conn_id)
        if info:
            info["last_heartbeat"] = time.time()

    def get_stale_connections(self, cutoff_time: float) -> list:
        """获取超过 cutoff_time 未心跳的连接 ID 列表"""
        stale = []
        for conn_id, info in self._connections.items():
            if info["user_id"] and info["last_heartbeat"] < cutoff_time:
                stale.append(conn_id)
        return stale

    @property
    def active_count(self) -> int:
        return len(self._connections)


class ChatServer:
    """聊天服务器主类"""

    def __init__(self, config: ServerConfig):
        self.config = config

        # 连接与业务管理器
        self.conn_manager = ConnectionManager()
        self.user_manager = UserManager(config.db_path)
        self.group_manager = GroupManager(config.db_path)
        self.msg_history = MessageHistory(config.db_path, config.recall_window)
        self.file_transfer = FileTransfer(config)
        self.msg_router = MessageRouter(
            self.conn_manager, self.msg_history,
            self.user_manager, self.group_manager,
        )
        self.p2p_helper = P2PHolePunchHelper(config)
        self.heartbeat = HeartbeatMonitor(
            self.conn_manager, self.user_manager,
            self.msg_router, config.heartbeat_interval,
            config.heartbeat_timeout, self.p2p_helper,
        )

        self._server = None

    # ---------------------------------------------------------------
    # 生命周期
    # ---------------------------------------------------------------

    async def start(self):
        """启动服务器"""
        self.config.ensure_dirs()
        await asyncio.to_thread(init_db, self.config.db_path)
        await self.user_manager.reset_online_statuses()

        self.heartbeat.start()

        self._server = await asyncio.start_server(
            self._handle_client,
            host=self.config.host,
            port=self.config.tcp_port,
        )

        addr = self._server.sockets[0].getsockname()
        logger.info("ChatServer listening on %s:%s", addr[0], addr[1])
        print(f"ChatServer started on {addr[0]}:{addr[1]}")

        async with self._server:
            await self._server.serve_forever()

    async def stop(self):
        """停止服务器"""
        self.heartbeat.stop()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        logger.info("ChatServer stopped")

    # ---------------------------------------------------------------
    # 客户端连接处理
    # ---------------------------------------------------------------

    async def _handle_client(self, reader, writer):
        """处理单个客户端连接的协程"""
        conn = Connection(reader, writer)
        conn_id = await self.conn_manager.add(conn, self.config.max_connections)
        if conn_id is None:
            try:
                await conn.send_message(
                    MessageType.ERROR,
                    {"code": 1, "message": "服务器连接数已满"},
                )
            finally:
                conn.close()
                await conn.wait_closed()
            return
        addr = conn.remote_addr
        logger.info("New connection #%s from %s", conn_id, addr)

        try:
            while True:
                msg = await conn.read_message()
                if msg is None:
                    break
                msg_type, seq, payload = msg
                await self._dispatch(conn_id, msg_type, seq, payload)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Connection #%s error: %s", conn_id, e)
        finally:
            await self._cleanup_connection(conn_id)

    async def _cleanup_connection(self, conn_id: int):
        """清理断开连接：更新在线状态、广播通知、移除连接"""
        user_id = self.conn_manager.get_user_id(conn_id)
        await self.conn_manager.remove(conn_id)
        if not user_id or self.conn_manager.get_by_user(user_id):
            return

        logger.info("Cleaning up connection #%s (user_id=%s)", conn_id, user_id)
        self.p2p_helper.unregister_user(user_id)
        await self.user_manager.set_online_status(user_id, False)

        # 关闭旧连接与新登录可能并发发生；广播离线前再确认一次，避免新连接被旧连接清理误伤。
        if self.conn_manager.get_by_user(user_id):
            await self.user_manager.set_online_status(user_id, True)
            return
        await self.msg_router.broadcast_online_status(user_id, False)

    # ---------------------------------------------------------------
    # 消息派发
    # ---------------------------------------------------------------

    async def _dispatch(self, conn_id: int, msg_type: int, seq: int, payload: dict):
        """根据消息类型派发到对应的处理方法"""
        try:
            if not isinstance(payload, dict):
                await self._send_error(conn_id, seq, "消息载荷格式无效")
                return
            if msg_type == MessageType.LOGIN_REQ:
                await self._handle_login(conn_id, seq, payload)
            elif msg_type == MessageType.REGISTER_REQ:
                await self._handle_register(conn_id, seq, payload)
            elif msg_type == MessageType.HEARTBEAT:
                await self._handle_heartbeat(conn_id, seq)
            elif msg_type == MessageType.PRIVATE_MSG:
                await self._handle_private_msg(conn_id, seq, payload)
            elif msg_type == MessageType.GROUP_MSG:
                await self._handle_group_msg(conn_id, seq, payload)
            elif msg_type == MessageType.FILE_INIT:
                await self._handle_file_init(conn_id, seq, payload)
            elif msg_type == MessageType.FILE_DATA:
                await self._handle_file_data(conn_id, seq, payload)
            elif msg_type == MessageType.FILE_ACK:
                await self._handle_file_ack(conn_id, seq, payload)
            elif msg_type == MessageType.GROUP_CREATE:
                await self._handle_group_create(conn_id, seq, payload)
            elif msg_type == MessageType.GROUP_JOIN:
                await self._handle_group_join(conn_id, seq, payload)
            elif msg_type == MessageType.GROUP_LEAVE:
                await self._handle_group_leave(conn_id, seq, payload)
            elif msg_type == MessageType.MSG_RECALL:
                await self._handle_msg_recall(conn_id, seq, payload)
            elif msg_type == MessageType.HISTORY_REQ:
                await self._handle_history_req(conn_id, seq, payload)
            elif msg_type == MessageType.ONLINE_USERS:
                await self._handle_online_users(conn_id, seq)
            elif msg_type == MessageType.AI_QUERY:
                await self._handle_ai_query(conn_id, seq, payload)
            elif msg_type == MessageType.P2P_HOLE_PUNCH:
                await self._handle_p2p_hole_punch(conn_id, seq, payload)
            else:
                await self._send_error(conn_id, seq, f"未知消息类型: {msg_type:#x}")
        except Exception as e:
            logger.error("Dispatch error for type=%s: %s", hex(msg_type), e, exc_info=True)
            await self._send_error(conn_id, seq, "服务器内部错误")

    async def _send_error(self, conn_id: int, seq: int, message: str):
        conn = self.conn_manager.get_conn(conn_id)
        if conn and not conn.is_closed:
            try:
                await conn.send_message(
                    MessageType.ERROR,
                    {"code": 1, "message": message},
                    seq=seq,
                )
            except Exception:
                pass

    # ---------------------------------------------------------------
    # 认证处理
    # ---------------------------------------------------------------

    async def _handle_login(self, conn_id: int, seq: int, payload: dict):
        username = payload.get("username", "")
        username = username.strip() if isinstance(username, str) else ""
        password_hash = payload.get("password_hash", "")
        current_user_id = self.conn_manager.get_user_id(conn_id)
        if current_user_id:
            current_user = await self.user_manager.get_user_info(current_user_id)
            if not current_user or current_user.get("username") != username:
                conn = self.conn_manager.get_conn(conn_id)
                if conn:
                    await conn.send_message(MessageType.LOGIN_RESP, {
                        "success": False,
                        "error": "当前连接已登录其他账号，请重新连接后再登录",
                    }, seq=seq)
                return
        result = await self.user_manager.login(username, password_hash)
        conn = self.conn_manager.get_conn(conn_id)
        if not conn:
            return
        if result["success"]:
            user_id = result["user_id"]
            await self.conn_manager.bind_user(conn_id, user_id)
            login_payload = {
                "success": True, "user_id": user_id, "username": username,
            }
            login_payload.update(await self._group_state_payload(user_id))
            await conn.send_message(MessageType.LOGIN_RESP, login_payload, seq=seq)
            # 注册 P2P 地址
            if conn.remote_addr:
                self.p2p_helper.register_user(user_id, conn.remote_addr)
            await self.msg_router.broadcast_online_status(user_id, True)
        else:
            await conn.send_message(MessageType.LOGIN_RESP, {
                "success": False, "error": result["error"],
            }, seq=seq)

    async def _group_state_payload(self, user_id: int) -> dict:
        """返回当前用户已加入群组和全部可加入群组，供客户端登录、重连和刷新侧边栏使用。"""
        user_groups = await self.group_manager.get_user_groups(user_id)
        all_groups = await self.group_manager.get_all_groups()
        groups = {str(g["id"]): g["name"] for g in user_groups}
        available_groups = {
            str(g["id"]): {
                "id": g["id"],
                "name": g["name"],
                "member_count": g.get("member_count", 0),
                "joined": str(g["id"]) in groups,
            }
            for g in all_groups
        }
        return {"groups": groups, "available_groups": available_groups}

    async def _handle_register(self, conn_id: int, seq: int, payload: dict):
        username = payload.get("username", "")
        username = username.strip() if isinstance(username, str) else ""
        password_hash = payload.get("password_hash", "")
        public_key = payload.get("public_key", "")
        result = await self.user_manager.register(username, password_hash, public_key)
        conn = self.conn_manager.get_conn(conn_id)
        if conn:
            await conn.send_message(MessageType.REGISTER_RESP, result, seq=seq)

    # ---------------------------------------------------------------
    # 心跳
    # ---------------------------------------------------------------

    async def _handle_heartbeat(self, conn_id: int, seq: int):
        self.conn_manager.update_heartbeat(conn_id)
        conn = self.conn_manager.get_conn(conn_id)
        if conn:
            await conn.send_message(MessageType.HEARTBEAT_ACK, {}, seq=seq)

    # ---------------------------------------------------------------
    # 消息处理
    # ---------------------------------------------------------------

    async def _send_private_rejection(
        self, conn_id: int, seq: int, from_id, to_id, message: str
    ):
        """私聊发送失败也用 PRIVATE_MSG ACK 回复，方便客户端更新 pending 状态。"""
        conn = self.conn_manager.get_conn(conn_id)
        if conn:
            await conn.send_message(MessageType.PRIVATE_MSG, {
                "from_id": from_id,
                "to_id": to_id,
                "msg_id": "",
                "timestamp": int(time.time()),
                "status": "rejected",
                "error": message,
                "_ack": True,
            }, seq=seq)

    async def _send_group_rejection(
        self, conn_id: int, seq: int, from_id, group_id, message: str
    ):
        """群聊发送失败也用 GROUP_MSG ACK 回复，避免发送方界面一直显示 pending。"""
        conn = self.conn_manager.get_conn(conn_id)
        if conn:
            await conn.send_message(MessageType.GROUP_MSG, {
                "from_id": from_id,
                "group_id": group_id,
                "msg_id": "",
                "timestamp": int(time.time()),
                "status": "rejected",
                "error": message,
                "_ack": True,
            }, seq=seq)

    async def _handle_private_msg(self, conn_id: int, seq: int, payload: dict):
        from_id = self.conn_manager.get_user_id(conn_id)
        if not from_id:
            await self._send_error(conn_id, seq, "未登录")
            return
        try:
            to_id = int(payload.get("to_id"))
        except (TypeError, ValueError):
            await self._send_private_rejection(conn_id, seq, from_id, None, "接收方ID无效")
            return
        if to_id <= 0:
            await self._send_private_rejection(conn_id, seq, from_id, to_id, "接收方ID无效")
            return
        if to_id == from_id:
            await self._send_private_rejection(conn_id, seq, from_id, to_id, "不能给自己发送私聊")
            return
        if not await self.user_manager.get_user_info(to_id):
            await self._send_private_rejection(conn_id, seq, from_id, to_id, "接收方不存在")
            return
        content = payload.get("content", "")
        if not isinstance(content, str) or not content.strip():
            await self._send_private_rejection(conn_id, seq, from_id, to_id, "消息内容为空")
            return
        client_msg_id = payload.get("msg_id", 0)

        result = await self.msg_router.route_private_msg(
            from_id, to_id, content, client_msg_id,
        )

        # 向发送方回复轻量 ACK（避免与真正转发的消息混淆）
        conn = self.conn_manager.get_conn(conn_id)
        if conn:
            await conn.send_message(MessageType.PRIVATE_MSG, {
                "from_id": from_id,
                "to_id": to_id,
                "msg_id": result["msg_id"],
                "timestamp": result["timestamp"],
                "status": result["status"],
                "_ack": True,
            }, seq=seq)

    async def _handle_group_msg(self, conn_id: int, seq: int, payload: dict):
        from_id = self.conn_manager.get_user_id(conn_id)
        if not from_id:
            await self._send_error(conn_id, seq, "未登录")
            return
        try:
            group_id = int(payload.get("group_id"))
        except (TypeError, ValueError):
            await self._send_group_rejection(conn_id, seq, from_id, None, "群组ID无效")
            return
        if group_id <= 0:
            await self._send_group_rejection(conn_id, seq, from_id, group_id, "群组ID无效")
            return
        content = payload.get("content", "")
        if not isinstance(content, str) or not content.strip():
            await self._send_group_rejection(conn_id, seq, from_id, group_id, "消息内容为空")
            return
        client_msg_id = payload.get("msg_id", 0)

        # 验证群成员身份
        is_member = await self.group_manager.is_member(group_id, from_id)
        if not is_member:
            await self._send_group_rejection(conn_id, seq, from_id, group_id, "不是群成员")
            return

        result = await self.msg_router.route_group_msg(
            from_id, group_id, content, client_msg_id,
        )

        # 向发送方回复轻量 ACK（群聊消息已在 route_group_msg 中广播给其他成员）
        conn = self.conn_manager.get_conn(conn_id)
        if conn:
            await conn.send_message(MessageType.GROUP_MSG, {
                "from_id": from_id,
                "group_id": group_id,
                "msg_id": result["msg_id"],
                "timestamp": result["timestamp"],
                "status": result["status"],
                "_ack": True,
            }, seq=seq)

    async def _handle_msg_recall(self, conn_id: int, seq: int, payload: dict):
        user_id = self.conn_manager.get_user_id(conn_id)
        if not user_id:
            await self._send_error(conn_id, seq, "未登录")
            return
        msg_id = payload.get("msg_id", "")
        if not isinstance(msg_id, str) or not msg_id.strip():
            conn = self.conn_manager.get_conn(conn_id)
            if conn:
                await conn.send_message(
                    MessageType.MSG_RECALL,
                    {"success": False, "error": "消息ID无效"},
                    seq=seq,
                )
            return
        result = await self.msg_history.recall_message(msg_id, user_id)
        conn = self.conn_manager.get_conn(conn_id)
        if conn:
            await conn.send_message(MessageType.MSG_RECALL, result, seq=seq)

        # 通知消息接收方或被撤回消息影响的其他用户
        if result.get("success"):
            recv_id = result.get("receiver_id")
            gid = result.get("group_id")
            notify = {"msg_id": msg_id, "recalled": True}
            if recv_id:
                await self.conn_manager.send_to_user(
                    recv_id, MessageType.MSG_RECALL, notify,
                )
            elif gid:
                await self.msg_router.send_to_group(
                    gid, MessageType.MSG_RECALL, notify,
                    exclude_user_id=user_id,
                )

    # ---------------------------------------------------------------
    # 文件传输
    # ---------------------------------------------------------------

    async def _handle_file_init(self, conn_id: int, seq: int, payload: dict):
        from_id = self.conn_manager.get_user_id(conn_id)
        if not from_id:
            await self._send_error(conn_id, seq, "未登录")
            return
        to_id = payload.get("to_id")
        filename = payload.get("filename", "")
        filesize = payload.get("filesize", 0)
        group_id = payload.get("group_id")
        client_file_id = payload.get("file_id")

        result = await self.file_transfer.init_transfer(
            from_id, to_id, filename, filesize, group_id, client_file_id,
        )

        # 回复发送方
        conn = self.conn_manager.get_conn(conn_id)
        if conn:
            await conn.send_message(MessageType.FILE_INIT, result, seq=seq)
        if result.get("success") and result.get("completed"):
            transfer = await self.file_transfer.get_transfer_progress(result["file_id"])
            await self._notify_file_completed(result["file_id"], transfer)

    async def _handle_file_data(self, conn_id: int, seq: int, payload: dict):
        sender_id = self.conn_manager.get_user_id(conn_id)
        if not sender_id:
            await self._send_error(conn_id, seq, "未登录")
            return
        file_id = payload.get("file_id")
        chunk_index = payload.get("chunk_index", 0)
        total_chunks = payload.get("total_chunks")
        data_b64 = payload.get("data", "")
        try:
            data = base64.b64decode(data_b64, validate=True)
        except Exception:
            await self._send_error(conn_id, seq, "文件数据编码错误")
            return

        result = await self.file_transfer.store_chunk(
            file_id,
            chunk_index,
            data,
            sender_id=sender_id,
            total_chunks=total_chunks,
        )
        conn = self.conn_manager.get_conn(conn_id)
        if conn:
            await conn.send_message(MessageType.FILE_DATA, result, seq=seq)

        # 文件传输完成，通知接收方
        if result.get("completed"):
            transfer = await self.file_transfer.get_transfer_progress(file_id)
            await self._notify_file_completed(file_id, transfer)

    async def _handle_file_ack(self, conn_id: int, seq: int, payload: dict):
        requester_id = self.conn_manager.get_user_id(conn_id)
        if not requester_id:
            await self._send_error(conn_id, seq, "未登录")
            return
        file_id = payload.get("file_id")
        offset = payload.get("offset", 0)

        result = await self.file_transfer.get_chunk(
            file_id,
            offset,
            requester_id=requester_id,
        )
        conn = self.conn_manager.get_conn(conn_id)
        if not conn:
            return
        if result.get("success"):
            encoded = base64.b64encode(result["data"]).decode("ascii")
            await conn.send_message(MessageType.FILE_ACK, {
                "file_id": file_id,
                "offset": offset,
                "data": encoded,
                "size": result["size"],
            }, seq=seq)
        else:
            await conn.send_message(MessageType.FILE_ACK, result, seq=seq)

    async def _notify_file_completed(self, file_id: str, transfer: dict):
        if not transfer:
            return
        payload = {
            "file_id": file_id,
            "from_id": transfer["sender_id"],
            "filename": transfer["filename"],
            "filesize": transfer["filesize"],
            "status": "completed",
        }
        group_id = transfer.get("group_id")
        if group_id:
            payload.update({
                "group_id": group_id,
                "related_type": "group",
                "related_target": str(group_id),
                "chat_key": f"group:{group_id}",
            })
            await self.msg_router.send_to_group(
                group_id, MessageType.FILE_INIT, payload,
                exclude_user_id=transfer["sender_id"],
            )
            return
        receiver_id = transfer.get("receiver_id")
        if receiver_id:
            payload.update({
                "related_type": "private",
                "related_target": str(transfer["sender_id"]),
                "chat_key": f"private:{transfer['sender_id']}",
            })
            await self.conn_manager.send_to_user(
                receiver_id, MessageType.FILE_INIT, payload
            )

    # ---------------------------------------------------------------
    # 群组管理
    # ---------------------------------------------------------------

    async def _handle_group_create(self, conn_id: int, seq: int, payload: dict):
        user_id = self.conn_manager.get_user_id(conn_id)
        if not user_id:
            await self._send_error(conn_id, seq, "未登录")
            return
        name = payload.get("name", "")
        result = await self.group_manager.create_group(name, user_id)
        conn = self.conn_manager.get_conn(conn_id)
        if conn:
            await conn.send_message(MessageType.GROUP_CREATE, result, seq=seq)

    async def _handle_group_join(self, conn_id: int, seq: int, payload: dict):
        user_id = self.conn_manager.get_user_id(conn_id)
        if not user_id:
            await self._send_error(conn_id, seq, "未登录")
            return
        try:
            group_id = int(payload.get("group_id"))
            if group_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            await self._send_error(conn_id, seq, "群组ID无效")
            return
        result = await self.group_manager.join_group(group_id, user_id)
        conn = self.conn_manager.get_conn(conn_id)
        if conn:
            await conn.send_message(MessageType.GROUP_JOIN, result, seq=seq)

    async def _handle_group_leave(self, conn_id: int, seq: int, payload: dict):
        user_id = self.conn_manager.get_user_id(conn_id)
        if not user_id:
            await self._send_error(conn_id, seq, "未登录")
            return
        try:
            group_id = int(payload.get("group_id"))
            if group_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            await self._send_error(conn_id, seq, "群组ID无效")
            return
        result = await self.group_manager.leave_group(group_id, user_id)
        result.setdefault("group_id", group_id)
        conn = self.conn_manager.get_conn(conn_id)
        if conn:
            await conn.send_message(MessageType.GROUP_LEAVE, result, seq=seq)

    # ---------------------------------------------------------------
    # 历史消息
    # ---------------------------------------------------------------

    async def _handle_history_req(self, conn_id: int, seq: int, payload: dict):
        user_id = self.conn_manager.get_user_id(conn_id)
        if not user_id:
            await self._send_error(conn_id, seq, "未登录")
            return
        chat_type = payload.get("type", payload.get("target_type", "private"))
        if chat_type not in {"private", "group"}:
            await self._send_error(conn_id, seq, "历史记录类型无效")
            return
        try:
            target_id = int(payload.get("target_id"))
            if target_id <= 0:
                raise ValueError
            limit = min(int(payload.get("limit", 50)), 200)
            if limit <= 0:
                raise ValueError
            before_id = payload.get("before_id")
            if before_id not in (None, ""):
                before_id = int(before_id)
                if before_id <= 0:
                    raise ValueError
            else:
                before_id = None
        except (TypeError, ValueError):
            await self._send_error(conn_id, seq, "历史记录参数无效")
            return

        if chat_type == "group":
            is_member = await self.group_manager.is_member(target_id, user_id)
            if not is_member:
                await self._send_error(conn_id, seq, "不是群成员")
                return
            messages = await self.msg_history.get_group_history(target_id, limit, before_id)
        else:
            if target_id == user_id:
                await self._send_error(conn_id, seq, "不能查询自己和自己的私聊")
                return
            if not await self.user_manager.get_user_info(target_id):
                await self._send_error(conn_id, seq, "目标用户不存在")
                return
            messages = await self.msg_history.get_private_history(user_id, target_id, limit, before_id)

        conn = self.conn_manager.get_conn(conn_id)
        if conn:
            await conn.send_message(MessageType.HISTORY_RESP, {
                "messages": messages,
                "type": chat_type,
                "target_id": target_id,
            }, seq=seq)

    # ---------------------------------------------------------------
    # 在线用户
    # ---------------------------------------------------------------

    async def _handle_online_users(self, conn_id: int, seq: int):
        user_id = self.conn_manager.get_user_id(conn_id)
        if not user_id:
            await self._send_error(conn_id, seq, "未登录")
            return
        users = await self.user_manager.get_online_users()
        payload = {
            "users": users,
            "count": len(users),
        }
        payload.update(await self._group_state_payload(user_id))
        conn = self.conn_manager.get_conn(conn_id)
        if conn:
            await conn.send_message(MessageType.ONLINE_USERS, payload, seq=seq)

    # ---------------------------------------------------------------
    # AI 智能回复
    # ---------------------------------------------------------------

    @staticmethod
    def _add_ai_speaker_alias(aliases: set, value):
        """记录可能被模型误当前缀的群成员显示名。纯数字不作为别名，避免误删“1: ...”编号列表。"""
        if value is None:
            return
        alias = str(value).strip()
        if not alias or alias.isdigit():
            return
        aliases.add(alias)

    @staticmethod
    def _strip_ai_speaker_prefix(reply: str, aliases: set) -> str:
        """群聊 AI 回复应直接显示内容；若模型模仿“用户名: 内容”格式，这里在广播前统一清理。"""
        if not isinstance(reply, str):
            return ""
        text = reply.strip()
        if not text or not aliases:
            return text

        # 最多连续清理三层前缀，覆盖 “Bob: Alice: ...” 这类模型复读格式，同时避免无限循环。
        for _ in range(3):
            matched = False
            for alias in sorted(aliases, key=len, reverse=True):
                if not alias:
                    continue
                head = text[:len(alias)]
                if head.casefold() != alias.casefold():
                    continue
                rest = text[len(alias):].lstrip()
                if rest.startswith(":") or rest.startswith("："):
                    text = rest[1:].lstrip()
                    matched = True
                    break
            if not matched:
                break
        return text

    async def _handle_ai_query(self, conn_id: int, seq: int, payload: dict):
        user_id = self.conn_manager.get_user_id(conn_id)
        if not user_id:
            await self._send_error(conn_id, seq, "未登录")
            return

        query = payload.get("query", "")
        if not isinstance(query, str) or not query.strip():
            await self._send_error(conn_id, seq, "查询内容为空")
            return
        query = query.strip()
        if len(query) > 8000:
            await self._send_error(conn_id, seq, "查询内容过长")
            return

        group_id = payload.get("group_id")
        if group_id not in (None, "", 0, "0"):
            try:
                group_id = int(group_id)
                if group_id <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                await self._send_error(conn_id, seq, "群组ID无效")
                return
            is_member = await self.group_manager.is_member(group_id, user_id)
            if not is_member:
                await self._send_error(conn_id, seq, "不是群成员")
                return
        else:
            group_id = None

        context = payload.get("context")
        if context is not None and not isinstance(context, list):
            await self._send_error(conn_id, seq, "上下文格式无效")
            return

        from server.ai_service import get_ai_service
        ai = get_ai_service(self.config)
        if not ai.available:
            await self._send_error(conn_id, seq, "AI 服务未配置")
            return

        # 获取用户信息用于上下文
        user_info = await self.user_manager.get_user_info(user_id)
        username = user_info.get("username", f"用户{user_id}") if user_info else f"用户{user_id}"

        speaker_aliases = set()
        self._add_ai_speaker_alias(speaker_aliases, username)
        self._add_ai_speaker_alias(speaker_aliases, f"User#{user_id}")
        self._add_ai_speaker_alias(speaker_aliases, f"用户{user_id}")
        group_member_names = {}
        if group_id:
            try:
                members = await self.group_manager.get_group_members(group_id)
            except Exception:
                members = []
            for member in members:
                member_id = member.get("id") or member.get("user_id")
                member_name = member.get("username")
                if member_id is not None and member_name:
                    group_member_names[member_id] = member_name
                self._add_ai_speaker_alias(speaker_aliases, member_name)
                if member_id is not None:
                    self._add_ai_speaker_alias(speaker_aliases, f"User#{member_id}")
                    self._add_ai_speaker_alias(speaker_aliases, f"用户{member_id}")

        # 获取群聊历史作为上下文
        history = []
        if group_id:
            history.append({
                "role": "system",
                "content": (
                    "以下内容是群聊历史上下文，仅用于理解问题。请以 AI Assistant 身份直接回答，"
                    "不要在回复开头添加任何群成员姓名、User#编号或“姓名:”格式前缀。"
                ),
            })
        # 优先使用客户端携带的会话上下文
        if context:
            for item in context[-10:]:
                if not isinstance(item, dict):
                    continue
                role = item.get("role")
                content = item.get("content")
                if role in {"user", "assistant"} and isinstance(content, str):
                    history.append({"role": role, "content": content[:8000]})
        elif group_id:
            msgs = await self.msg_history.get_group_history(group_id, limit=10)
            for m in msgs:
                role = "user"
                if m.get("recalled", 0) == 1:
                    continue
                if m.get("sender_id") == user_id:
                    role = "user"
                sender_id = m["sender_id"]
                sender_name = group_member_names.get(sender_id)
                if not sender_name:
                    sender_info = await self.user_manager.get_user_info(sender_id)
                    sender_name = sender_info.get("username", str(sender_id)) if sender_info else str(sender_id)
                content = str(m.get("content", ""))
                history.append({
                    "role": role,
                    "content": f"群聊历史消息（发送者：{sender_name}）：{content}"[:8000],
                })

        reply = await ai.query_with_context(query, username=username, history=history)
        if group_id:
            reply = self._strip_ai_speaker_prefix(reply, speaker_aliases)

        # 回复发起者
        conn = self.conn_manager.get_conn(conn_id)
        if conn:
            await conn.send_message(MessageType.AI_RESP, {
                "query": query,
                "reply": reply,
                "content": reply,
                "group_id": group_id,
                "related_type": "group" if group_id else None,
                "related_target": str(group_id) if group_id else None,
                "chat_key": f"group:{group_id}" if group_id else None,
            }, seq=seq)

        # 如果在群聊中，也广播给群成员
        if group_id:
            await self.msg_router.send_to_group(
                group_id, MessageType.AI_RESP,
                {
                    "from_id": user_id,
                    "reply": reply,
                    "content": reply,
                    "group_id": group_id,
                    "related_type": "group",
                    "related_target": str(group_id),
                    "chat_key": f"group:{group_id}",
                },
                exclude_user_id=user_id,
            )

    # ---------------------------------------------------------------
    # P2P 打洞（使用 P2PHolePunchHelper 协调双方地址交换）
    # ---------------------------------------------------------------

    async def _handle_p2p_hole_punch(self, conn_id: int, seq: int, payload: dict):
        from_id = self.conn_manager.get_user_id(conn_id)
        if not from_id:
            await self._send_error(conn_id, seq, "未登录")
            return
        target_id = payload.get("target_id")
        if not target_id:
            await self._send_error(conn_id, seq, "缺少目标用户")
            return
        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            await self._send_error(conn_id, seq, "目标用户ID无效")
            return
        if target_id <= 0 or target_id == from_id:
            await self._send_error(conn_id, seq, "目标用户ID无效")
            return

        conn = self.conn_manager.get_conn(conn_id)
        target_conn = self.conn_manager.get_by_user(target_id)
        payload = dict(payload)
        payload["user_id"] = from_id
        if not payload.get("addr") and conn and conn.remote_addr:
            payload["addr"] = f"{conn.remote_addr[0]}:{conn.remote_addr[1]}"

        ok = await self.p2p_helper.handle_hole_punch(
            req_conn=conn,
            payload=payload,
            target_conn=target_conn,
        )

        # 同时通知目标用户有 P2P 连接请求
        if ok:
            if target_conn:
                await self.p2p_helper.notify_p2p_ready(
                    user_id=from_id,
                    target_id=target_id,
                    target_conn=target_conn,
                )
        else:
            await self._send_error(conn_id, seq, "P2P 打洞失败")
