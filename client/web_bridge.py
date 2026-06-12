"""
WebView 桥接层 — Python 与 JavaScript 双向通信中枢

消息流向：
  JS → pywebview.api.method() → WebBridge.method() → handler.send_*() → server
  server → handler callback → WebBridge._on_*() → evaluate_js() → JS 渲染

所有暴露给 JS 的方法在类上定义，pywebview 通过 js_api 自动注册。
"""

import json
import os
import threading
import time
import uuid

import webview

from protocol import MessageType


def _now() -> int:
    return int(time.time())


class WebBridge:
    """Python 侧桥接：接收 JS 命令、维护状态、推送服务器事件到 JS"""

    def __init__(self, conn, handler, store, p2p, download_dir):
        self.conn = conn
        self.handler = handler
        self.store = store
        self.p2p = p2p
        self._download_dir = download_dir
        os.makedirs(self._download_dir, exist_ok=True)

        # --- 会话状态 (与旧 gui.py 一致) ---
        self._user_id = None
        self._username = None
        self._password_hash = None
        self._logged_in = False
        self._online_users = {}  # name -> id
        self._groups = {}  # gid -> name
        self._messages = []
        self._current_target = None
        self._current_target_id = None
        self._chat_type = "private"
        self._pending_acks = {}
        self._last_sent_msg_id = None
        self._dl_state = {}

        self._register_callbacks()

    # =============================================================
    # 回调注册
    # =============================================================

    def _register_callbacks(self):
        reg = self.handler.register
        reg(MessageType.LOGIN_RESP, self._on_login_resp)
        reg(MessageType.REGISTER_RESP, self._on_register_resp)
        reg(MessageType.PRIVATE_MSG, self._on_private_msg)
        reg(MessageType.GROUP_MSG, self._on_group_msg)
        reg(MessageType.STATUS_UPDATE, self._on_status_update)
        reg(MessageType.AI_RESP, self._on_ai_resp)
        reg(MessageType.CONTENT_WARN, self._on_content_warn)
        reg(MessageType.ERROR, self._on_error)
        reg(MessageType.MSG_RECALL, self._on_recall)
        reg(MessageType.HISTORY_RESP, self._on_history)
        reg(MessageType.ONLINE_USERS, self._on_online_users)
        reg(MessageType.GROUP_CREATE, self._on_group_create_resp)
        reg(MessageType.GROUP_JOIN, self._on_group_join_resp)
        reg(MessageType.GROUP_LEAVE, self._on_group_leave_resp)
        reg(MessageType.FILE_INIT, self._on_file_init)
        reg(MessageType.FILE_ACK, self._on_file_ack)

        self.conn.on_disconnected(self._on_disconnected)
        self.conn.on_connected(self._on_reconnected)

        self.p2p.register_message_handler(self.handler)

    # =============================================================
    # 向 JS 推送事件
    # =============================================================

    def _push(self, event_type: str, data: dict):
        """向 JS 发送事件（线程安全 — pywebview evaluate_js 可在任意线程调用）"""
        try:
            payload = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
            # 寻找活动窗口
            for w in webview.windows:
                if w:
                    w.evaluate_js(f"window.__pyEvent({payload})")
                    return
        except Exception:
            pass  # WebView 尚未就绪时静默忽略

    def _push_msg(self, msg: dict):
        """推送一条消息对象到 JS（统一格式）"""
        self._push("new_message", msg)

    # =============================================================
    # 状态管理（与旧 gui.py 逻辑一致）
    # =============================================================

    def _append_and_store(self, msg: dict):
        self._messages.append(msg)
        if self._username:
            self.store.add_message(self._username, msg)

    def _remember_pending(self, send_result: dict, msg: dict):
        seq = send_result.get("seq") if send_result else None
        if seq is not None:
            self._pending_acks[seq] = msg

    def _apply_message_ack(self, seq: int, payload: dict):
        msg = self._pending_acks.pop(seq, None)
        if not msg:
            return
        local_msg_id = str(msg.get("local_msg_id", msg.get("msg_id", "")))
        msg["status"] = payload.get("status", "ack")
        if payload.get("timestamp"):
            msg["timestamp"] = payload["timestamp"]
        server_msg_id = str(payload.get("msg_id", "") or "")
        if not server_msg_id:
            return
        msg["server_msg_id"] = server_msg_id
        msg["msg_id"] = server_msg_id
        self._last_sent_msg_id = server_msg_id
        if self._username:
            self.store.update_message_id(
                self._username, local_msg_id, server_msg_id,
                timestamp=msg.get("timestamp"), status=msg.get("status", ""),
            )

    def _mark_recalled(self, msg_id: str):
        target = str(msg_id)
        if not target:
            return
        for msg in self._messages:
            ids = {
                str(msg.get("msg_id", "")),
                str(msg.get("local_msg_id", "")),
                str(msg.get("server_msg_id", "")),
            }
            if target in ids:
                msg["is_recalled"] = True
                msg["content"] = "[已撤回]"
        if self._username:
            self.store.mark_recalled(self._username, target)

    def _history_sender(self, msg: dict) -> str:
        sender_id = msg.get("sender_id", msg.get("from_id"))
        if sender_id == self._user_id:
            return self._username or "You"
        for name, uid in self._online_users.items():
            if uid == sender_id:
                return name
        return f"User#{sender_id}" if sender_id else msg.get("sender", "unknown")

    # =============================================================
    # 消息回调（在后台线程执行）
    # =============================================================

    def _on_login_resp(self, msg_type, seq, payload):
        if payload.get("success"):
            self._user_id = payload.get("user_id")
            self._username = payload.get("username", self._username)
            self._logged_in = True
            # 立即将自己加入在线列表（服务器可能还未返回 ONLINE_USERS）
            if self._username and self._user_id:
                self._online_users[self._username] = self._user_id
            # 将当前已有状态一起推送给 JS
            self._push("login_success", {
                "user_id": self._user_id,
                "username": self._username,
                "online_users": {k: v for k, v in self._online_users.items()},
                "groups": {k: v for k, v in self._groups.items()},
            })
            # 主动请求在线用户列表（响应会通过 _on_online_users 补充其他用户）
            self.handler.request_online_users()
        else:
            msg = payload.get("error") or payload.get("message", "Login failed")
            self._push("login_error", {"error": msg})

    def _on_register_resp(self, msg_type, seq, payload):
        if payload.get("success"):
            self._push("register_success", {"message": "Registration successful! Please login."})
        else:
            msg = payload.get("error") or payload.get("message", "Registration failed")
            self._push("register_error", {"error": msg})

    def _on_private_msg(self, msg_type, seq, payload):
        if payload.get("_ack"):
            self._apply_message_ack(seq, payload)
            return
        if payload.get("from_id") == self._user_id:
            return
        sender = payload.get("from_username", payload.get("sender", f"User#{payload.get('from_id', '')}"))
        msg = {
            "type": "private", "sender": sender,
            "content": payload.get("content", ""),
            "msg_id": str(payload.get("msg_id", "")),
            "timestamp": payload.get("timestamp", _now()),
            "from_id": payload.get("from_id", 0),
            "target_id": payload.get("from_id", 0),
        }
        self._append_and_store(msg)
        self._push_msg(msg)

    def _on_group_msg(self, msg_type, seq, payload):
        if payload.get("_ack"):
            self._apply_message_ack(seq, payload)
            return
        if payload.get("from_id") == self._user_id:
            return
        sender = payload.get("from_username", payload.get("sender", f"User#{payload.get('from_id', '')}"))
        gid = str(payload.get("group_id", ""))
        msg = {
            "type": "group", "group_id": gid,
            "group_name": self._groups.get(gid, f"Group#{gid}"),
            "sender": sender, "content": payload.get("content", ""),
            "msg_id": str(payload.get("msg_id", "")),
            "timestamp": payload.get("timestamp", _now()),
            "from_id": payload.get("from_id", 0),
            "target_id": gid,
        }
        self._append_and_store(msg)
        self._push_msg(msg)

    def _on_status_update(self, msg_type, seq, payload):
        username = payload.get("username", "")
        uid = payload.get("user_id", 0)
        is_online = payload.get("is_online", False)
        if is_online:
            self._online_users[username] = uid
        else:
            self._online_users.pop(username, None)
        self._push("status_update", {
            "username": username,
            "user_id": uid,
            "is_online": is_online,
            "online_users": {k: v for k, v in self._online_users.items()},
            "groups": {k: v for k, v in self._groups.items()},
        })

    def _on_ai_resp(self, msg_type, seq, payload):
        content = payload.get("content", "")
        if content:
            msg = {"type": "system", "content": f"[AI] {content}", "timestamp": _now()}
            self._append_and_store(msg)
            self._push_msg(msg)

    def _on_content_warn(self, msg_type, seq, payload):
        msg = {"type": "system", "content": f"[WARN] {payload.get('message', 'Content warning')}", "timestamp": _now()}
        self._push_msg(msg)

    def _on_error(self, msg_type, seq, payload):
        msg = {"type": "system", "content": f"[Error {payload.get('code', -1)}] {payload.get('message', '')}", "timestamp": _now()}
        self._push_msg(msg)

    def _on_recall(self, msg_type, seq, payload):
        if payload.get("success") is False:
            err = payload.get("error") or payload.get("message", "recall failed")
            self._push_msg({"type": "system", "content": f"Recall failed: {err}"})
            return
        msg_id = str(payload.get("msg_id", ""))
        self._mark_recalled(msg_id)
        mid = msg_id[:8]
        self._push("message_recalled", {"msg_id": msg_id})
        self._push_msg({"type": "system", "content": f"Message {mid}... was recalled"})

    def _on_history(self, msg_type, seq, payload):
        history = payload.get("messages", [])
        for m in history:
            self._messages.append(m)
        formatted = []
        for m in history:
            kind = "group" if payload.get("type") == "group" or m.get("group_id") else "private"
            formatted.append({
                "type": kind,
                "sender": self._history_sender(m),
                "content": "[已撤回]" if m.get("recalled") or m.get("is_recalled") else m.get("content", ""),
                "group_id": str(m.get("group_id", "")),
                "timestamp": m.get("timestamp", 0),
                "msg_id": str(m.get("msg_id", "")),
                "from_id": m.get("sender_id", m.get("from_id", 0)),
            })
        self._push("history", {
            "type": payload.get("type", "private"),
            "messages": formatted,
            "count": len(formatted),
        })

    def _on_online_users(self, msg_type, seq, payload):
        users = payload.get("users", [])
        self._online_users = {}
        for u in users:
            uid = u.get("id", 0)
            name = u.get("username", f"User#{uid}")
            self._online_users[name] = uid
        self._push("online_users", {
            "online_users": {k: v for k, v in self._online_users.items()},
            "groups": {k: v for k, v in self._groups.items()},
        })

    def _on_group_create_resp(self, msg_type, seq, payload):
        if payload.get("success"):
            gid = str(payload.get("group_id", ""))
            name = payload.get("name", "")
            self._groups[gid] = name
            self._push("group_created", {
                "group_id": gid, "name": name,
                "groups": {k: v for k, v in self._groups.items()},
            })
            self._push_msg({"type": "system", "content": f"Group '{name}' created (ID: {gid})"})
        else:
            err = payload.get("error") or payload.get("message", "")
            self._push_msg({"type": "system", "content": f"Create group failed: {err}"})

    def _on_group_join_resp(self, msg_type, seq, payload):
        if payload.get("success"):
            gid = str(payload.get("group_id", ""))
            name = payload.get("name", gid)
            self._groups[gid] = name
            self._push("group_joined", {
                "group_id": gid, "name": name,
                "groups": {k: v for k, v in self._groups.items()},
            })
            self._push_msg({"type": "system", "content": f"Joined group '{name}'"})
        else:
            err = payload.get("error") or payload.get("message", "")
            self._push_msg({"type": "system", "content": f"Join failed: {err}"})

    def _on_group_leave_resp(self, msg_type, seq, payload):
        gid = str(payload.get("group_id", ""))
        if payload.get("success"):
            self._groups.pop(gid, None)
            self._push("group_left", {
                "group_id": gid,
                "groups": {k: v for k, v in self._groups.items()},
            })
            self._push_msg({"type": "system", "content": f"Left group {gid}"})
        else:
            err = payload.get("error") or payload.get("message", "")
            self._push_msg({"type": "system", "content": f"Leave failed: {err}"})

    def _on_file_init(self, msg_type, seq, payload):
        status = payload.get("status", "")
        if status != "completed":
            return
        file_id = payload.get("file_id", "")
        from_id = payload.get("from_id", 0)
        filename = payload.get("filename", "unknown")
        filesize = payload.get("filesize", 0)
        sender = f"User#{from_id}"
        for name, uid in self._online_users.items():
            if uid == from_id:
                sender = name
                break
        self._push("file_incoming", {
            "file_id": file_id,
            "filename": filename,
            "filesize": filesize,
            "sender": sender,
            "from_id": from_id,
        })
        threading.Thread(target=self._gui_download_file,
                         args=(file_id, filename, filesize), daemon=True).start()

    def _on_file_ack(self, msg_type, seq, payload):
        file_id = payload.get("file_id", "")
        offset = payload.get("offset", 0)
        data_b64 = payload.get("data", "")
        if payload.get("success") is False or not data_b64:
            return
        state = self._dl_state.get(file_id)
        if not state:
            return
        import base64
        data = base64.b64decode(data_b64)
        state["data"][offset] = data
        state["remaining"] -= len(data)
        if state["remaining"] <= 0:
            state["event"].set()

    def _gui_download_file(self, file_id, filename, filesize):
        CHUNK_SIZE = 64 * 1024
        state = {"data": {}, "remaining": filesize,
                 "event": threading.Event(), "chunk_size": CHUNK_SIZE}
        self._dl_state[file_id] = state
        for offset in range(0, filesize, CHUNK_SIZE):
            self.handler.request_file_chunk(file_id, offset)
        if not state["event"].wait(timeout=30):
            self._push("file_download_result", {
                "filename": filename, "success": False,
                "error": "Download timed out",
            })
            self._dl_state.pop(file_id, None)
            return
        dest = os.path.join(self._download_dir, filename)
        try:
            os.makedirs(self._download_dir, exist_ok=True)
            with open(dest, "wb") as f:
                for offset in sorted(state["data"].keys()):
                    f.write(state["data"][offset])
            self._push("file_download_result", {
                "filename": filename, "filesize": filesize,
                "success": True, "path": dest,
            })
        except Exception as e:
            self._push("file_download_result", {
                "filename": filename, "success": False, "error": str(e),
            })
        self._dl_state.pop(file_id, None)

    def _on_disconnected(self):
        self._push("connection_status", {"status": "disconnected"})

    def _on_reconnected(self):
        self._push("connection_status", {"status": "reconnected"})
        if self._username and self._password_hash:
            self.handler.send_login(self._username, self._password_hash)

    # =============================================================
    # JS → Python API (通过 pywebview js_api 暴露)
    # =============================================================

    def login(self, username: str, password: str) -> dict:
        """JS 调用：登录"""
        self._username = username
        self._password_hash = password
        self.handler.send_login(username, password)
        return {"ok": True}

    def register(self, username: str, password: str) -> dict:
        """JS 调用：注册"""
        self._password_hash = password
        self.handler.send_register(username, password)
        return {"ok": True}

    def send_private_msg(self, target_id: int, content: str) -> dict:
        """JS 调用：发送私聊消息"""
        if not self._user_id:
            return {"ok": False, "error": "Not logged in"}
        result = self.handler.send_private_msg(self._user_id, target_id, content)
        local_msg_id = str(result.get("client_msg_id", "")) if result else ""
        msg = {
            "type": "private", "sender": self._username or "You",
            "receiver": None, "target_id": target_id,
            "content": content, "local_msg_id": local_msg_id,
            "msg_id": local_msg_id, "timestamp": _now(),
            "status": "pending", "from_id": self._user_id,
        }
        self._append_and_store(msg)
        self._remember_pending(result, msg)
        self._push_msg(msg)
        return {"ok": True, "msg": msg}

    def send_group_msg(self, group_id: int, content: str) -> dict:
        """JS 调用：发送群聊消息"""
        if not self._user_id:
            return {"ok": False, "error": "Not logged in"}
        result = self.handler.send_group_msg(self._user_id or 0, group_id, content)
        local_msg_id = str(result.get("client_msg_id", "")) if result else ""
        gid = str(group_id)
        msg = {
            "type": "group", "group_id": gid,
            "group_name": self._groups.get(gid, f"Group#{gid}"),
            "sender": self._username or "You", "content": content,
            "local_msg_id": local_msg_id, "msg_id": local_msg_id,
            "timestamp": _now(), "status": "pending",
            "from_id": self._user_id, "target_id": gid,
        }
        self._append_and_store(msg)
        self._remember_pending(result, msg)
        self._push_msg(msg)
        return {"ok": True, "msg": msg}

    def send_ai_query(self, query: str, group_id: int = 0) -> dict:
        """JS 调用：发送 AI 查询"""
        if not self._user_id:
            return {"ok": False, "error": "Not logged in"}
        self.handler.send_ai_query(self._user_id, group_id, query)
        return {"ok": True}

    def request_history(self, target_type: str, target_id: int) -> dict:
        """JS 调用：请求历史消息"""
        self.handler.request_history(target_type, target_id)
        return {"ok": True}

    def request_online_users(self) -> dict:
        """JS 调用：请求在线用户列表"""
        self.handler.request_online_users()
        return {"ok": True}

    def group_create(self, name: str) -> dict:
        """JS 调用：创建群组"""
        if not self._user_id:
            return {"ok": False, "error": "Not logged in"}
        self.handler.group_create(name, self._user_id)
        return {"ok": True}

    def group_join(self, group_id: int) -> dict:
        """JS 调用：加入群组"""
        if not self._user_id:
            return {"ok": False, "error": "Not logged in"}
        self.handler.group_join(group_id, self._user_id)
        return {"ok": True}

    def group_leave(self, group_id: int) -> dict:
        """JS 调用：退出群组"""
        if not self._user_id:
            return {"ok": False, "error": "Not logged in"}
        self.handler.group_leave(group_id, self._user_id)
        return {"ok": True}

    def send_recall(self, msg_id: str) -> dict:
        """JS 调用：撤回消息"""
        self.handler.send_recall(msg_id)
        return {"ok": True}

    async def select_and_send_file(self) -> dict:
        """JS 调用：打开文件选择对话框并发送文件（pywebview 6.x 协程版）"""
        try:
            # create_file_dialog 是 pywebview 6.x 的协程，需要 await
            result = await webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False,
                title="Select file to send"
            )
            if not result or not result[0]:
                return {"ok": False, "error": "No file selected"}
            filepath = result[0]
            filesize = os.path.getsize(filepath)
            filename = os.path.basename(filepath)
            file_id = str(uuid.uuid4())
            # 获取当前聊天目标
            target_id = self._current_target_id
            if not target_id or not self._user_id:
                return {"ok": False, "error": "No target selected"}
            self.handler.send_file_init(self._user_id, target_id, filename, filesize, file_id)
            # 后台发送
            import threading
            threading.Thread(target=self._send_file_worker,
                             args=(filepath, file_id, filesize, filename), daemon=True).start()
            return {"ok": True, "filename": filename, "filesize": filesize}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _send_file_worker(self, filepath, file_id, filesize, filename):
        chunk_size = 64 * 1024
        with open(filepath, "rb") as f:
            idx = 0
            total = (filesize + chunk_size - 1) // chunk_size
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                self.handler.send_file_data(file_id, chunk, idx, total)
                idx += 1
                time.sleep(0.01)
        self._push("file_sent", {"filename": filename, "filesize": filesize})

    def set_current_target(self, target_name: str, target_id, chat_type: str):
        """JS 调用：切换当前聊天目标"""
        self._current_target = target_name
        self._current_target_id = target_id
        self._chat_type = chat_type
        return {"ok": True}

    def get_initial_state(self) -> dict:
        """JS 调用：获取初始状态"""
        return {
            "online_users": {k: v for k, v in self._online_users.items()},
            "groups": {k: v for k, v in self._groups.items()},
            "username": self._username,
            "user_id": self._user_id,
            "connected": self.conn.is_connected,
        }

    def get_connection_status(self) -> dict:
        """JS 调用：获取连接状态"""
        return {
            "connected": self.conn.is_connected,
            "host": self.conn.host if hasattr(self.conn, 'host') else "unknown",
            "port": self.conn.port if hasattr(self.conn, 'port') else 0,
        }

    def get_online_users_snapshot(self) -> dict:
        """JS 调用：获取当前在线用户快照"""
        return {
            "online_users": {k: v for k, v in self._online_users.items()},
            "groups": {k: v for k, v in self._groups.items()},
        }
