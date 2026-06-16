"""
服务端 P2P 打洞协助模块
负责在客户端之间协调 UDP 打洞，交换彼此的地址信息
"""

import asyncio
import logging
from typing import Optional, Dict, Tuple

from server.config import ServerConfig
from server.protocol import (
    Connection,
    MessageType,
    make_p2p_hole_punch_payload,
)

logger = logging.getLogger(__name__)


class P2PHolePunchHelper:
    """
    P2P UDP 打洞协助器
    维护在线用户的地址映射，协助 NAT 穿透
    """

    def __init__(self, config: ServerConfig):
        self.config = config
        # user_id -> (host, port) TCP 连接地址，用于协助打洞
        self._online_users: Dict[int, Tuple[str, int]] = {}
        # user_id -> (udp_host, udp_port) UDP 地址
        self._udp_addrs: Dict[int, Tuple[str, int]] = {}

    def register_user(self, user_id: int, tcp_addr: Tuple[str, int]):
        """注册用户及其 TCP 连接地址"""
        self._online_users[user_id] = tcp_addr
        logger.info(f"P2P: 用户 {user_id} 已注册，地址 {tcp_addr}")

    def register_udp_addr(self, user_id: int, udp_addr: Tuple[str, int]):
        """注册用户的 UDP 地址"""
        self._udp_addrs[user_id] = udp_addr
        logger.info(f"P2P: 用户 {user_id} UDP 地址 {udp_addr}")

    def unregister_user(self, user_id: int):
        """用户下线时清理"""
        self._online_users.pop(user_id, None)
        self._udp_addrs.pop(user_id, None)

    def get_user_addr(self, user_id: int) -> Optional[Tuple[str, int]]:
        """获取用户的 TCP 连接地址"""
        return self._online_users.get(user_id)

    def get_udp_addr(self, user_id: int) -> Optional[Tuple[str, int]]:
        """获取用户的 UDP 地址"""
        return self._udp_addrs.get(user_id)

    def is_online(self, user_id: int) -> bool:
        """检查用户是否在线"""
        return user_id in self._online_users

    async def handle_hole_punch(
        self,
        req_conn: Connection,
        payload: dict,
        target_conn: Optional[Connection] = None,
    ) -> bool:
        """
        处理 P2P 打洞请求

        流程:
        1. 收到 A 的打洞请求 (user_id=发起者ID, target_id=目标ID)
        2. 查询 A 和 B 的地址
        3. 把 B 的地址发给 A
        4. 把 A 的地址发给 B（双方都需要对方地址才能双向打洞）
        5. 双方收到对方地址后开始互发 UDP 包穿透 NAT

        Args:
            req_conn: 发起者的连接
            payload: {user_id, target_id, addr(optional)}
            target_conn: 目标用户的连接（如果已知）

        Returns:
            是否成功
        """
        user_id = payload.get("user_id")
        target_id = payload.get("target_id")
        udp_addr_str = payload.get("addr", "")

        if not user_id or not target_id:
            logger.warning(f"P2P 打洞请求参数不完整: {payload}")
            return False

        # 如果包含 UDP 地址，先注册
        if udp_addr_str:
            try:
                host, port_str = udp_addr_str.rsplit(":", 1)
                self.register_udp_addr(user_id, (host, int(port_str)))
            except (ValueError, AttributeError):
                logger.warning(f"P2P: UDP 地址格式无效: {udp_addr_str}")

        # 检查目标是否在线
        if not self.is_online(target_id):
            logger.warning(f"P2P: 目标用户 {target_id} 不在线")
            await req_conn.send_message(
                MessageType.P2P_HOLE_PUNCH,
                {
                    "user_id": user_id,
                    "target_id": target_id,
                    "error": "target_offline",
                    "message": "目标用户不在线",
                },
            )
            return False

        # 获取双方的地址（优先 UDP，无则用 TCP）
        initiator_addr = self.get_udp_addr(user_id) or self.get_user_addr(user_id)
        target_addr = self.get_udp_addr(target_id) or self.get_user_addr(target_id)

        if not initiator_addr or not target_addr:
            logger.warning(f"P2P: 无法获取双方地址")
            return False

        # 告诉发起者目标地址
        await req_conn.send_message(
            MessageType.P2P_HOLE_PUNCH,
            make_p2p_hole_punch_payload(
                user_id=target_id,
                target_id=user_id,
                addr=f"{target_addr[0]}:{target_addr[1]}",
            ),
        )
        logger.info(f"P2P: 已向发起者 {user_id} 发送目标 {target_id} 的地址 {target_addr}")

        # 告诉目标用户发起者的地址（这是打洞成功的关键：双方都需要互知地址）
        if target_conn:
            await target_conn.send_message(
                MessageType.P2P_HOLE_PUNCH,
                make_p2p_hole_punch_payload(
                    user_id=user_id,
                    target_id=target_id,
                    addr=f"{initiator_addr[0]}:{initiator_addr[1]}",
                ),
            )
            logger.info(f"P2P: 已通知目标 {target_id} 发起者 {user_id} 的地址 {initiator_addr}")
        else:
            logger.warning(f"P2P: 无法通知目标 {target_id}（未提供连接）")

        logger.info(f"P2P: 打洞请求完成 {user_id} <-> {target_id}")
        return True

    async def notify_p2p_ready(
        self,
        user_id: int,
        target_id: int,
        target_conn: Connection,
    ):
        """
        通知目标用户有 P2P 连接请求

        Args:
            user_id: 发起者 ID
            target_id: 目标用户 ID
            target_conn: 目标用户的连接
        """
        try:
            initiator_addr = self.get_user_addr(user_id)
            initiator_addr_str = f"{initiator_addr[0]}:{initiator_addr[1]}" if initiator_addr else ""

            await target_conn.send_message(
                MessageType.P2P_READY,
                {
                    "from_id": user_id,
                    "addr": initiator_addr_str,
                    "message": f"用户 {user_id} 想与您建立 P2P 直连",
                },
            )
            logger.info(f"P2P: 已通知 {target_id} 来自 {user_id} 的连接请求")
        except Exception as e:
            logger.error(f"P2P: 通知 {target_id} 失败: {e}")
