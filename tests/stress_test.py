"""
压力测试脚本：模拟多个虚拟客户端并发连接服务器。

使用方法：
    python tests/stress_test.py             # 默认参数
    python tests/stress_test.py --clients 50 --host 127.0.0.1 --port 8888

测试流程：
    每个虚拟客户端：连接 -> 注册 -> 登录 -> 发消息 -> 收消息 -> 断开
    统计指标：连接成功率、消息延迟、吞吐量
"""

import argparse
import asyncio
import json
import logging
import random
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# 确保可以导入 server 模块（仅添加项目根目录）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.protocol import (
    HEADER_FORMAT,
    HEADER_SIZE,
    MAGIC,
    MessageType,
    decode_messages,
    encode_message,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("stress_test")


# ── 数据结构 ──────────────────────────────────────────────────────


@dataclass
class Stats:
    """单个客户端的测试统计"""

    client_id: int
    connected: bool = False
    registered: bool = False
    logged_in: bool = False
    messages_sent: int = 0
    messages_received: int = 0
    errors: list[str] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)
    timeline: dict[str, float] = field(default_factory=dict)


@dataclass
class AggregateReport:
    """聚合测试报告"""

    total_clients: int = 0
    connected: int = 0
    login_success: int = 0
    total_messages_sent: int = 0
    total_messages_received: int = 0
    total_errors: int = 0
    avg_latency: float = 0.0
    p50_latency: float = 0.0
    p99_latency: float = 0.0
    max_latency: float = 0.0
    min_latency: float = 0.0
    throughput: float = 0.0  # msg/s
    duration: float = 0.0


# ── 虚拟客户端 ─────────────────────────────────────────────────────


class VirtualClient:
    """
    模拟一个客户端完整生命周期:
    连接 -> 注册 -> 登录 -> 发消息 -> 收消息 -> 断开
    """

    def __init__(
        self,
        client_id: int,
        host: str,
        port: int,
        timeout: float = 10.0,
        messages_per_client: int = 5,
    ):
        self.client_id = client_id
        self.host = host
        self.port = port
        self.timeout = timeout
        self.messages_per_client = messages_per_client
        self.stats = Stats(client_id=client_id)
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._username = f"stress_user_{client_id}_{int(time.time())}"
        self._password = "stress_pass"
        self._buffer = b""

    async def run(self) -> Stats:
        """执行完整的客户端测试流程"""
        try:
            if not await self._connect():
                return self.stats
            if not await self._register():
                return self.stats
            if not await self._login():
                return self.stats
            await self._exchange_messages()
        except asyncio.TimeoutError:
            self.stats.errors.append("timeout")
        except ConnectionResetError:
            self.stats.errors.append("connection_reset")
        except Exception as e:
            self.stats.errors.append(f"unexpected:{e}")
        finally:
            await self._disconnect()
        return self.stats

    async def _connect(self) -> bool:
        """建立 TCP 连接"""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout,
            )
            self.stats.connected = True
            self.stats.timeline["connected"] = time.time()
            return True
        except Exception as e:
            self.stats.errors.append(f"connect_failed:{e}")
            return False

    async def _send_and_wait(self, msg_type: int, payload: dict, timeout: float = 5.0) -> Optional[tuple]:
        """发送消息并等待响应"""
        data = encode_message(msg_type, payload)
        self._writer.write(data)
        await self._writer.drain()

        # 从流中读取响应
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                chunk = await asyncio.wait_for(
                    self._reader.read(4096),
                    max(0.1, deadline - time.time()),
                )
            except asyncio.TimeoutError:
                return None

            if not chunk:
                return None

            self._buffer += chunk
            messages, self._buffer = decode_messages(self._buffer)
            if messages:
                return messages[0]

        return None

    async def _register(self) -> bool:
        """发送注册请求"""
        start = time.time()
        payload = {"username": self._username, "password_hash": self._password}
        response = await self._send_and_wait(MessageType.REGISTER_REQ, payload)
        latency = time.time() - start

        if response:
            msg_type, seq, resp_payload = response
            self.stats.registered = True
            self.stats.timeline["registered"] = time.time()
            self.stats.latencies.append(latency)
            return True

        self.stats.errors.append("register_no_response")
        return False

    async def _login(self) -> bool:
        """发送登录请求"""
        start = time.time()
        payload = {"username": self._username, "password_hash": self._password}
        response = await self._send_and_wait(MessageType.LOGIN_REQ, payload)
        latency = time.time() - start

        if response:
            msg_type, seq, resp_payload = response
            self.stats.logged_in = True
            self.stats.timeline["logged_in"] = time.time()
            self.stats.latencies.append(latency)
            return True

        self.stats.errors.append("login_no_response")
        return False

    async def _exchange_messages(self):
        """发送并接收消息（简化为只发不收，或发给自己）"""
        for i in range(self.messages_per_client):
            start = time.time()
            payload = {
                "from_id": self.client_id,
                "to_id": self.client_id,  # 发给自己
                "content": f"stress_test_msg_{i}_from_{self.client_id}",
                "msg_id": hash(f"stress_{self.client_id}_{i}") & 0x7FFFFFFF,
                "timestamp": int(time.time()),
            }
            data = encode_message(MessageType.PRIVATE_MSG, payload)
            self._writer.write(data)
            await self._writer.drain()
            latency = time.time() - start

            self.stats.messages_sent += 1
            self.stats.latencies.append(latency)

        self.stats.timeline["messages_done"] = time.time()

    async def _disconnect(self):
        """关闭连接"""
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass


# ── 测试调度器 ─────────────────────────────────────────────────────


class StressTester:
    """压力测试调度器"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8888,
        num_clients: int = 10,
        concurrency: int = 10,
        messages_per_client: int = 5,
        timeout: float = 10.0,
    ):
        self.host = host
        self.port = port
        self.num_clients = num_clients
        self.concurrency = concurrency
        self.messages_per_client = messages_per_client
        self.timeout = timeout

    async def run(self) -> AggregateReport:
        """执行压力测试"""
        logger.info(
            "Starting stress test | clients=%d concurrency=%d msgs/client=%d",
            self.num_clients,
            self.concurrency,
            self.messages_per_client,
        )

        start_time = time.time()
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _run_client(client_id: int) -> Stats:
            async with semaphore:
                client = VirtualClient(
                    client_id=client_id,
                    host=self.host,
                    port=self.port,
                    timeout=self.timeout,
                    messages_per_client=self.messages_per_client,
                )
                return await client.run()

        tasks = [_run_client(i) for i in range(self.num_clients)]
        results = await asyncio.gather(*tasks)
        duration = time.time() - start_time

        return self._aggregate(results, duration)

    def _aggregate(self, results: list[Stats], duration: float) -> AggregateReport:
        """汇总所有客户端统计"""
        report = AggregateReport(duration=duration)
        all_latencies: list[float] = []

        for s in results:
            if s.connected:
                report.connected += 1
            if s.logged_in:
                report.login_success += 1
            report.total_messages_sent += s.messages_sent
            report.total_messages_received += s.messages_received
            report.total_errors += len(s.errors)
            all_latencies.extend(s.latencies)

        report.total_clients = len(results)
        report.total_messages_received = report.total_messages_sent  # 简化统计

        if all_latencies:
            all_latencies.sort()
            report.avg_latency = sum(all_latencies) / len(all_latencies)
            report.min_latency = all_latencies[0]
            report.max_latency = all_latencies[-1]
            report.p50_latency = all_latencies[len(all_latencies) // 2]
            report.p99_latency = all_latencies[int(len(all_latencies) * 0.99)]
            report.throughput = (
                report.total_messages_received / duration if duration > 0 else 0
            )

        return report


# ── 报告输出 ─────────────────────────────────────────────────────


def print_report(report: AggregateReport):
    """格式化输出测试报告"""
    separator = "=" * 60
    print(f"\n{separator}")
    print("  STRESS TEST REPORT")
    print(separator)
    print(f"  Duration:              {report.duration:.2f} s")
    print(f"  Total Clients:         {report.total_clients}")
    print(f"  Connected:             {report.connected}")
    print(f"  Login Success:         {report.login_success}")
    print(f"  Messages Sent:         {report.total_messages_sent}")
    print(f"  Messages Received:     {report.total_messages_received}")
    print(f"  Total Errors:          {report.total_errors}")
    print(separator)
    print("  -- Latency (seconds) --")
    print(f"  Avg Latency:           {report.avg_latency:.4f}")
    print(f"  Min Latency:           {report.min_latency:.4f}")
    print(f"  Max Latency:           {report.max_latency:.4f}")
    print(f"  P50 Latency:           {report.p50_latency:.4f}")
    print(f"  P99 Latency:           {report.p99_latency:.4f}")
    print(separator)
    print(f"  Throughput:            {report.throughput:.2f} msg/s")
    print(separator)

    # 错误汇总
    if report.total_errors > 0:
        print(f"\n  WARNING: {report.total_errors} errors occurred!")
        print(f"  Connect success rate: {report.connected / report.total_clients * 100:.1f}%")
        print(f"  Login success rate:   {report.login_success / report.total_clients * 100:.1f}%")
        print(separator)


# ── CLI ────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="简聊压力测试工具")
    parser.add_argument("--host", default="127.0.0.1", help="服务器地址")
    parser.add_argument("--port", type=int, default=8888, help="服务器端口")
    parser.add_argument("--clients", type=int, default=10, help="虚拟客户端数量")
    parser.add_argument("--concurrency", type=int, default=10, help="并发连接数")
    parser.add_argument("--messages", type=int, default=5, help="每个客户端发送消息数")
    parser.add_argument("--timeout", type=float, default=10.0, help="超时秒数")
    return parser.parse_args()


async def main():
    args = parse_args()
    tester = StressTester(
        host=args.host,
        port=args.port,
        num_clients=args.clients,
        concurrency=args.concurrency,
        messages_per_client=args.messages,
        timeout=args.timeout,
    )
    report = await tester.run()
    print_report(report)


if __name__ == "__main__":
    asyncio.run(main())
