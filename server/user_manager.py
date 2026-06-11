"""
用户管理模块
提供用户注册、登录、在线状态管理等功能。
"""
import time
import asyncio
from server.database import get_db


class UserManager:
    """用户管理器，封装所有用户相关操作"""

    def __init__(self, db_path: str):
        self._db_path = db_path

    async def register(self, username: str, password_hash: str, public_key: str = "") -> dict:
        """注册新用户，用户名唯一性由数据库 UNIQUE 约束保证"""
        def _run():
            with get_db(self._db_path) as conn:
                cur = conn.execute("SELECT id FROM users WHERE username = ?", (username,))
                if cur.fetchone():
                    return {"success": False, "error": "用户名已存在"}
                now = time.time()
                cur = conn.execute(
                    "INSERT INTO users (username, password_hash, public_key, created_at) VALUES (?, ?, ?, ?)",
                    (username, password_hash, public_key, now),
                )
                conn.commit()
                return {"success": True, "user_id": cur.lastrowid}
        return await asyncio.to_thread(_run)

    async def login(self, username: str, password_hash: str) -> dict:
        """登录验证，成功返回 user_id，失败返回错误信息"""
        def _run():
            with get_db(self._db_path) as conn:
                cur = conn.execute(
                    "SELECT id, password_hash, is_online FROM users WHERE username = ?",
                    (username,),
                )
                row = cur.fetchone()
                if not row:
                    return {"success": False, "error": "用户不存在"}
                user_id, stored_hash = row["id"], row["password_hash"]
                if stored_hash != password_hash:
                    return {"success": False, "error": "密码错误"}
                now = time.time()
                conn.execute(
                    "UPDATE users SET last_login = ?, is_online = 1 WHERE id = ?",
                    (now, user_id),
                )
                conn.commit()
                return {"success": True, "user_id": user_id}
        return await asyncio.to_thread(_run)

    async def logout(self, user_id: int):
        """用户登出，清除在线状态"""
        def _run():
            with get_db(self._db_path) as conn:
                conn.execute("UPDATE users SET is_online = 0 WHERE id = ?", (user_id,))
                conn.commit()
        return await asyncio.to_thread(_run)

    async def get_online_users(self) -> list:
        """获取所有在线用户列表"""
        def _run():
            with get_db(self._db_path) as conn:
                cur = conn.execute(
                    "SELECT id, username, public_key FROM users WHERE is_online = 1",
                )
                return [dict(r) for r in cur.fetchall()]
        return await asyncio.to_thread(_run)

    async def get_user_info(self, user_id: int) -> dict:
        """获取用户基本信息，不存在时返回 None"""
        def _run():
            with get_db(self._db_path) as conn:
                cur = conn.execute(
                    "SELECT id, username, public_key, created_at, is_online FROM users WHERE id = ?",
                    (user_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None
        return await asyncio.to_thread(_run)

    async def set_online_status(self, user_id: int, status: bool):
        """设置用户的在线状态"""
        def _run():
            with get_db(self._db_path) as conn:
                conn.execute(
                    "UPDATE users SET is_online = ? WHERE id = ?",
                    (1 if status else 0, user_id),
                )
                conn.commit()
        return await asyncio.to_thread(_run)
