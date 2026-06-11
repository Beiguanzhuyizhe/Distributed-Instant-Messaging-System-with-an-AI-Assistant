"""
消息历史管理模块
提供消息的持久化存储、历史查询和撤回功能。
"""
import time
import asyncio
import uuid
from server.database import get_db


class MessageHistory:
    """消息历史管理器"""

    def __init__(self, db_path: str, recall_window: int = 120):
        self._db_path = db_path
        self._recall_window = recall_window  # 撤回时间窗口（秒）

    def _gen_msg_id(self) -> str:
        return str(uuid.uuid4())

    async def store_message(
        self,
        sender_id: int,
        receiver_id: int,
        group_id: int,
        msg_type: int,
        content: str,
        msg_id: str = None,
        is_encrypted: int = 0,
    ) -> str:
        """存储消息到数据库，返回 msg_id（始终使用服务端 UUID，避免多客户端冲突）"""
        actual_msg_id = self._gen_msg_id()
        now = time.time()

        def _run():
            with get_db(self._db_path) as conn:
                conn.execute(
                    """INSERT INTO messages
                       (msg_id, sender_id, receiver_id, group_id, msg_type, content, is_encrypted, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (actual_msg_id, sender_id, receiver_id, group_id, msg_type, content, is_encrypted, now),
                )
                conn.commit()
                return actual_msg_id
        return await asyncio.to_thread(_run)

    async def get_private_history(
        self, user_id: int, target_id: int, limit: int = 50, before_id: int = None
    ) -> list:
        """获取私聊历史消息，按时间正序返回"""
        def _run():
            with get_db(self._db_path) as conn:
                where = """(sender_id = ? AND receiver_id = ?)
                            OR (sender_id = ? AND receiver_id = ?)"""
                params = [user_id, target_id, target_id, user_id]
                if before_id:
                    where += " AND id < ?"
                    params.append(before_id)
                query = f"SELECT * FROM messages WHERE {where} ORDER BY id DESC LIMIT ?"
                params.append(limit)
                cur = conn.execute(query, params)
                rows = [dict(r) for r in cur.fetchall()]
                rows.reverse()
                return rows
        return await asyncio.to_thread(_run)

    async def get_group_history(
        self, group_id: int, limit: int = 50, before_id: int = None
    ) -> list:
        """获取群聊历史消息，按时间正序返回"""
        def _run():
            with get_db(self._db_path) as conn:
                where = "group_id = ?"
                params = [group_id]
                if before_id:
                    where += " AND id < ?"
                    params.append(before_id)
                query = f"SELECT * FROM messages WHERE {where} ORDER BY id DESC LIMIT ?"
                params.append(limit)
                cur = conn.execute(query, params)
                rows = [dict(r) for r in cur.fetchall()]
                rows.reverse()
                return rows
        return await asyncio.to_thread(_run)

    async def recall_message(self, msg_id: str, user_id: int) -> dict:
        """撤回消息（2分钟内可撤回），成功时返回消息关联信息用于通知"""
        def _run():
            with get_db(self._db_path) as conn:
                cur = conn.execute("SELECT * FROM messages WHERE msg_id = ?", (msg_id,))
                row = cur.fetchone()
                if not row:
                    return {"success": False, "error": "消息不存在"}
                msg = dict(row)
                if msg["sender_id"] != user_id:
                    return {"success": False, "error": "只能撤回自己的消息"}
                now = time.time()
                if now - msg["created_at"] > self._recall_window:
                    return {"success": False, "error": "超过撤回时间窗口"}
                conn.execute("UPDATE messages SET recalled = 1 WHERE msg_id = ?", (msg_id,))
                conn.commit()
                return {
                    "success": True,
                    "msg_id": msg_id,
                    "receiver_id": msg["receiver_id"],
                    "group_id": msg["group_id"],
                }
        return await asyncio.to_thread(_run)
