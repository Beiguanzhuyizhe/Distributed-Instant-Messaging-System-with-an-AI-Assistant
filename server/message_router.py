"""
消息路由模块
负责私聊、群聊消息的转发和在线状态广播。
集成内容审核、AI 智能回复。
"""
import time
import logging
from server.protocol import MessageType

logger = logging.getLogger(__name__)


class MessageRouter:
    """消息路由器，处理消息的存储与转发"""

    def __init__(self, conn_manager, msg_history, user_manager, group_manager):
        self.conn_manager = conn_manager
        self.msg_history = msg_history
        self.user_manager = user_manager
        self.group_manager = group_manager
        self._config = None
        self._moderator = None

    def _get_moderator(self):
        if self._moderator is None:
            from server.content_moderator import get_moderator
            self._moderator = get_moderator()
        return self._moderator

    def _moderate(self, content: str) -> dict:
        """审核消息内容，返回 {"rejected": bool, "level": str, "clean_content": str}"""
        from server.config import ServerConfig
        if self._config is None:
            self._config = ServerConfig()
        if not self._config.enable_content_moderation:
            return {"rejected": False, "level": "low", "clean_content": content}

        moderator = self._get_moderator()
        result = moderator.moderate(content)
        if result.passed:
            return {"rejected": False, "level": "low", "clean_content": content}

        if result.level == "high":
            logger.info(f"内容审核拦截 (high): {result.reason}")
            return {"rejected": True, "level": "high", "clean_content": content}

        # mid 级别：替换敏感词后放行
        clean = moderator.replace_sensitive(content)
        return {"rejected": False, "level": "mid", "clean_content": clean}

    async def route_private_msg(self, from_id: int, to_id: int, content: str,
                                client_msg_id=0) -> dict:
        """转发私聊消息：先存储，再尝试直接转发给在线接收方"""
        # 内容审核
        mod = self._moderate(content)
        if mod["rejected"]:
            await self.conn_manager.send_to_user(
                from_id, MessageType.CONTENT_WARN,
                {
                    "message": "消息包含违规内容，已被拦截",
                    "level": mod["level"],
                    "related_type": "private",
                    "related_target": str(to_id),
                    "chat_key": f"private:{to_id}",
                },
            )
            return {"msg_id": "", "timestamp": 0, "status": "rejected"}
        content = mod["clean_content"]

        msg_id = await self.msg_history.store_message(
            sender_id=from_id,
            receiver_id=to_id,
            group_id=None,
            msg_type=MessageType.PRIVATE_MSG,
            content=content,
            msg_id=str(client_msg_id) if client_msg_id and client_msg_id != 0 else None,
        )

        now_ts = int(time.time())

        # 获取发送方用户名
        sender_info = await self.user_manager.get_user_info(from_id)
        from_username = sender_info.get("username", f"User#{from_id}") if sender_info else f"User#{from_id}"

        payload = {
            "from_id": from_id,
            "from_username": from_username,
            "to_id": to_id,
            "content": content,
            "msg_id": msg_id,
            "timestamp": now_ts,
        }

        sent = await self.conn_manager.send_to_user(
            to_id, MessageType.PRIVATE_MSG, payload
        )

        return {
            "msg_id": msg_id,
            "timestamp": now_ts,
            "status": "delivered" if sent else "stored",
        }

    async def route_group_msg(self, from_id: int, group_id: int, content: str,
                              client_msg_id=0) -> dict:
        """转发群聊消息：存储后广播给群内所有其他成员"""
        # 内容审核
        mod = self._moderate(content)
        if mod["rejected"]:
            await self.conn_manager.send_to_user(
                from_id, MessageType.CONTENT_WARN,
                {
                    "message": "消息包含违规内容，已被拦截",
                    "level": mod["level"],
                    "related_type": "group",
                    "related_target": str(group_id),
                    "chat_key": f"group:{group_id}",
                    "group_id": str(group_id),
                },
            )
            return {"msg_id": "", "timestamp": 0, "status": "rejected"}
        content = mod["clean_content"]

        msg_id = await self.msg_history.store_message(
            sender_id=from_id,
            receiver_id=None,
            group_id=group_id,
            msg_type=MessageType.GROUP_MSG,
            content=content,
            msg_id=str(client_msg_id) if client_msg_id and client_msg_id != 0 else None,
        )

        now_ts = int(time.time())

        sender_info = await self.user_manager.get_user_info(from_id)
        from_username = sender_info.get("username", f"User#{from_id}") if sender_info else f"User#{from_id}"

        payload = {
            "from_id": from_id,
            "from_username": from_username,
            "group_id": group_id,
            "content": content,
            "msg_id": msg_id,
            "timestamp": now_ts,
        }

        members = await self.group_manager.get_group_members(group_id)
        for member in members:
            if member["id"] != from_id:
                await self.conn_manager.send_to_user(
                    member["id"], MessageType.GROUP_MSG, payload
                )

        return {"msg_id": msg_id, "timestamp": now_ts, "status": "sent"}

    async def broadcast_online_status(self, user_id: int, is_online: bool):
        """向所有在线用户广播某用户的在线状态变化"""
        user_info = await self.user_manager.get_user_info(user_id)
        if not user_info:
            return
        payload = {
            "user_id": user_id,
            "username": user_info["username"],
            "is_online": 1 if is_online else 0,
        }
        await self.conn_manager.broadcast(
            MessageType.STATUS_UPDATE, payload, exclude_user_id=user_id
        )

    async def send_to_group(self, group_id: int, msg_type: int, payload: dict,
                            exclude_user_id: int = None):
        """向群组内所有成员发送消息（用于状态通知等）"""
        members = await self.group_manager.get_group_members(group_id)
        for member in members:
            if exclude_user_id is None or member["id"] != exclude_user_id:
                await self.conn_manager.send_to_user(
                    member["id"], msg_type, payload
                )
