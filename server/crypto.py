"""
服务端加密辅助模块
管理用户公钥存储与查询，协助 P2P 加密通信
"""

import logging
from typing import Optional

from server.database import get_db
from server.config import ServerConfig

logger = logging.getLogger(__name__)


class KeyManager:
    """
    公钥管理器
    用于存储和查询用户的 RSA 公钥
    公钥在用户注册时上传，存储在 users 表的 public_key 字段
    """

    def __init__(self, config: ServerConfig):
        self.config = config

    def store_public_key(self, user_id: int, pubkey_pem: str) -> bool:
        """
        存储用户的公钥

        Args:
            user_id: 用户 ID
            pubkey_pem: 公钥 PEM 字符串

        Returns:
            是否成功
        """
        try:
            with get_db(self.config.db_path) as conn:
                conn.execute(
                    "UPDATE users SET public_key = ? WHERE id = ?",
                    (pubkey_pem, user_id),
                )
                conn.commit()
            logger.info(f"已存储用户 {user_id} 的公钥")
            return True
        except Exception as e:
            logger.error(f"存储公钥失败 (user_id={user_id}): {e}")
            return False

    def get_public_key(self, user_id: int) -> Optional[str]:
        """
        获取用户的公钥

        Args:
            user_id: 用户 ID

        Returns:
            公钥 PEM 字符串，未找到返回 None
        """
        try:
            with get_db(self.config.db_path) as conn:
                cursor = conn.execute(
                    "SELECT public_key FROM users WHERE id = ?",
                    (user_id,),
                )
                row = cursor.fetchone()
                if row and row["public_key"]:
                    return row["public_key"]
                return None
        except Exception as e:
            logger.error(f"获取公钥失败 (user_id={user_id}): {e}")
            return None

    def has_public_key(self, user_id: int) -> bool:
        """检查用户是否已上传公钥"""
        return self.get_public_key(user_id) is not None

    def delete_public_key(self, user_id: int) -> bool:
        """删除用户公钥（用户注销时）"""
        try:
            with get_db(self.config.db_path) as conn:
                conn.execute(
                    "UPDATE users SET public_key = '' WHERE id = ?",
                    (user_id,),
                )
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"删除公钥失败 (user_id={user_id}): {e}")
            return False

    def get_public_keys_batch(self, user_ids: list) -> dict:
        """
        批量获取用户公钥

        Args:
            user_ids: 用户 ID 列表

        Returns:
            {user_id: pubkey_pem, ...}
        """
        if not user_ids:
            return {}

        try:
            placeholders = ",".join("?" * len(user_ids))
            with get_db(self.config.db_path) as conn:
                cursor = conn.execute(
                    f"SELECT id, public_key FROM users WHERE id IN ({placeholders})",
                    user_ids,
                )
                result = {}
                for row in cursor.fetchall():
                    if row["public_key"]:
                        result[row["id"]] = row["public_key"]
                return result
        except Exception as e:
            logger.error(f"批量获取公钥失败: {e}")
            return {}
