"""
群组管理模块
提供群组的创建、加入、退出、成员查询等功能。
"""
import time
import asyncio
from server.database import get_db


class GroupManager:
    """群组管理器，封装所有群组相关操作"""

    def __init__(self, db_path: str):
        self._db_path = db_path

    async def create_group(self, name: str, owner_id: int) -> dict:
        """创建群组，创建者自动成为群主并加入群组"""
        if not isinstance(name, str) or not name.strip():
            return {"success": False, "error": "群名称不能为空"}
        name = name.strip()
        if len(name) > 128:
            return {"success": False, "error": "群名称过长"}

        def _run():
            with get_db(self._db_path) as conn:
                now = time.time()
                cur = conn.execute(
                    "INSERT INTO groups (name, owner_id, created_at) VALUES (?, ?, ?)",
                    (name, owner_id, now),
                )
                group_id = cur.lastrowid
                conn.execute(
                    "INSERT INTO group_members (group_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)",
                    (group_id, owner_id, "owner", now),
                )
                conn.commit()
                return {"success": True, "group_id": group_id, "name": name}
        return await asyncio.to_thread(_run)

    async def join_group(self, group_id: int, user_id: int) -> dict:
        """加入群组"""
        def _run():
            with get_db(self._db_path) as conn:
                cur = conn.execute("SELECT id, name FROM groups WHERE id = ?", (group_id,))
                row = cur.fetchone()
                if not row:
                    return {"success": False, "error": "群组不存在"}
                group_name = row["name"]
                cur = conn.execute(
                    "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
                    (group_id, user_id),
                )
                if cur.fetchone():
                    return {"success": False, "error": "已是群成员"}
                now = time.time()
                conn.execute(
                    "INSERT INTO group_members (group_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)",
                    (group_id, user_id, "member", now),
                )
                conn.commit()
                return {"success": True, "group_id": group_id, "name": group_name}
        return await asyncio.to_thread(_run)

    async def leave_group(self, group_id: int, user_id: int) -> dict:
        """退出群组。群主不能退出，需先转让群主身份"""
        def _run():
            with get_db(self._db_path) as conn:
                cur = conn.execute(
                    "SELECT role FROM group_members WHERE group_id = ? AND user_id = ?",
                    (group_id, user_id),
                )
                row = cur.fetchone()
                if not row:
                    return {"success": False, "error": "不是群成员"}
                if row["role"] == "owner":
                    return {"success": False, "error": "群主不能退出群组"}
                conn.execute(
                    "DELETE FROM group_members WHERE group_id = ? AND user_id = ?",
                    (group_id, user_id),
                )
                conn.commit()
                return {"success": True}
        return await asyncio.to_thread(_run)

    async def get_group_members(self, group_id: int) -> list:
        """获取群组所有成员信息"""
        def _run():
            with get_db(self._db_path) as conn:
                cur = conn.execute(
                    """SELECT u.id, u.username, u.public_key, gm.role, gm.joined_at
                       FROM group_members gm
                       JOIN users u ON u.id = gm.user_id
                       WHERE gm.group_id = ?""",
                    (group_id,),
                )
                return [dict(r) for r in cur.fetchall()]
        return await asyncio.to_thread(_run)

    async def get_user_groups(self, user_id: int) -> list:
        """获取用户加入的所有群组"""
        def _run():
            with get_db(self._db_path) as conn:
                cur = conn.execute(
                    """SELECT g.id, g.name, g.owner_id, gm.role, gm.joined_at
                       FROM group_members gm
                       JOIN groups g ON g.id = gm.group_id
                       WHERE gm.user_id = ?""",
                    (user_id,),
                )
                return [dict(r) for r in cur.fetchall()]
        return await asyncio.to_thread(_run)

    async def is_member(self, group_id: int, user_id: int) -> bool:
        """检查用户是否为群组成员"""
        def _run():
            with get_db(self._db_path) as conn:
                cur = conn.execute(
                    "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
                    (group_id, user_id),
                )
                return cur.fetchone() is not None
        return await asyncio.to_thread(_run)
