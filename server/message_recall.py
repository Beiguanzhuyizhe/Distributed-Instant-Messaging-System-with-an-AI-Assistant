"""
消息撤回模块
服务端处理 MSG_RECALL 请求:
1. 验证消息是否存在
2. 验证撤回者是否是发送者本人
3. 检查是否在 2 分钟撤回窗口内
4. 标记消息为已撤回
5. 通知相关客户端
"""

import time
import logging
from typing import Optional

from server.database import get_db
from server.config import ServerConfig
from server.protocol import MessageType, Connection

logger = logging.getLogger(__name__)


def recall_message(
    msg_id: str,
    requester_id: int,
    config: ServerConfig,
    notify_callback: Optional[callable] = None,
) -> dict:
    """
    处理消息撤回请求

    Args:
        msg_id: 消息 ID（客户端生成的唯一 ID）
        requester_id: 请求撤回的用户 ID
        config: 服务端配置
        notify_callback: 通知回调函数，用于通知相关客户端
                        签名: notify_callback(msg_type, payload, target_user_ids)

    Returns:
        {"success": bool, "error": Optional[str], "msg_info": Optional[dict]}
    """
    db_path = config.db_path
    recall_window = config.recall_window  # 默认 120 秒

    try:
        with get_db(db_path) as conn:
            # 1. 查找消息
            cursor = conn.execute(
                "SELECT id, sender_id, created_at, recalled, receiver_id, "
                "group_id, msg_type FROM messages WHERE msg_id = ?",
                (msg_id,),
            )
            row = cursor.fetchone()

            if not row:
                logger.warning(f"撤回失败: 消息不存在 msg_id={msg_id}")
                return {"success": False, "error": "MSG_NOT_FOUND"}

            msg_db_id = row["id"]
            sender_id = row["sender_id"]
            created_at = row["created_at"]
            already_recalled = row["recalled"]
            receiver_id = row["receiver_id"]
            group_id = row["group_id"]

            # 2. 验证撤回者是否是发送者本人
            if requester_id != sender_id:
                logger.warning(
                    f"撤回失败: 非发送者撤回 msg_id={msg_id}, "
                    f"sender={sender_id}, requester={requester_id}"
                )
                return {"success": False, "error": "NOT_SENDER"}

            # 3. 检查是否已撤回
            if already_recalled:
                logger.info(f"消息已被撤回: msg_id={msg_id}")
                return {"success": True, "error": None, "msg_info": _msg_info(row)}

            # 4. 检查是否在撤回窗口内
            now = time.time()
            elapsed = now - created_at
            if elapsed > recall_window:
                logger.warning(
                    f"撤回失败: 超时 msg_id={msg_id}, "
                    f"elapsed={elapsed:.1f}s, window={recall_window}s"
                )
                return {"success": False, "error": "RECALL_TIMEOUT"}

            # 5. 标记为已撤回
            conn.execute(
                "UPDATE messages SET recalled = 1 WHERE id = ?",
                (msg_db_id,),
            )
            conn.commit()

            logger.info(f"消息撤回成功: msg_id={msg_id}, sender={sender_id}")

            msg_info = _msg_info(row)

            # 6. 通知相关客户端
            if notify_callback:
                recall_payload = {
                    "msg_id": msg_id,
                    "sender_id": sender_id,
                    "receiver_id": receiver_id,
                    "group_id": group_id,
                }

                if group_id:
                    # 群聊：通知所有群成员（通过回调由 server 处理）
                    notify_callback(
                        MessageType.MSG_RECALL,
                        recall_payload,
                        target_group_id=group_id,
                        exclude_user_id=sender_id,
                    )
                elif receiver_id:
                    # 私聊：通知接收者
                    notify_callback(
                        MessageType.MSG_RECALL,
                        recall_payload,
                        target_user_ids=[receiver_id],
                    )

            return {"success": True, "error": None, "msg_info": msg_info}

    except Exception as e:
        logger.error(f"撤回处理异常: {e}")
        return {"success": False, "error": "INTERNAL_ERROR"}


def _msg_info(row) -> dict:
    """从数据库行提取消息信息"""
    return {
        "id": row["id"],
        "sender_id": row["sender_id"],
        "receiver_id": row["receiver_id"],
        "group_id": row["group_id"],
        "created_at": row["created_at"],
    }


async def handle_recall_request(
    conn: Connection,
    payload: dict,
    requester_id: int,
    config: ServerConfig,
    notify_callback: Optional[callable] = None,
):
    """
    处理客户端的撤回请求（高层封装）

    Args:
        conn: 请求者的连接
        payload: {"msg_id": str}
        requester_id: 请求者用户 ID
        config: 服务端配置
        notify_callback: 通知回调
    """
    msg_id = payload.get("msg_id", "")
    if not msg_id:
        await conn.send_message(
            MessageType.ERROR,
            {"code": 1, "message": "缺少 msg_id"},
        )
        return

    result = recall_message(msg_id, requester_id, config, notify_callback)

    if result["success"]:
        await conn.send_message(
            MessageType.MSG_RECALL,
            {"msg_id": msg_id, "success": True},
        )
    else:
        error_code = {
            "MSG_NOT_FOUND": 12,
            "NOT_SENDER": 1,
            "RECALL_TIMEOUT": 13,
        }.get(result["error"], 1)

        await conn.send_message(
            MessageType.ERROR,
            {"code": error_code, "message": result["error"]},
        )
