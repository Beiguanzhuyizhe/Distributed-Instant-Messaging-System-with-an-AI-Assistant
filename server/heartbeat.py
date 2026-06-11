"""
心跳检测模块
后台循环检查所有连接的心跳状态，超时连接自动清理。
"""
import asyncio
import time
import logging

logger = logging.getLogger(__name__)


class HeartbeatMonitor:
    """心跳检测器，后台任务周期性检查连接活性"""

    def __init__(self, conn_manager, user_manager, msg_router,
                 check_interval: int = 5, timeout: int = 30):
        self.conn_manager = conn_manager
        self.user_manager = user_manager
        self.msg_router = msg_router
        self._check_interval = check_interval
        self._timeout = timeout
        self._task = None

    def start(self):
        """启动后台心跳检测任务"""
        if self._task is None:
            self._task = asyncio.create_task(self._run())
            logger.info("Heartbeat monitor started (interval=%ds, timeout=%ds)",
                        self._check_interval, self._timeout)

    def stop(self):
        """停止心跳检测任务"""
        if self._task:
            self._task.cancel()
            self._task = None
            logger.info("Heartbeat monitor stopped")

    async def _run(self):
        """心跳检测主循环"""
        while True:
            try:
                await asyncio.sleep(self._check_interval)
                await self._check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Heartbeat check error: %s", e)

    async def _check(self):
        """检查所有连接，清理超时的离线连接"""
        now = time.time()
        cutoff = now - self._timeout
        stale_conn_ids = self.conn_manager.get_stale_connections(cutoff)

        for conn_id in stale_conn_ids:
            user_id = self.conn_manager.get_user_id(conn_id)
            if user_id:
                logger.info("Heartbeat timeout for user %s (conn_id=%s), cleaning up", user_id, conn_id)
                await self.user_manager.set_online_status(user_id, False)
                await self.msg_router.broadcast_online_status(user_id, False)
            await self.conn_manager.remove(conn_id)
