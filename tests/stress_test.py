"""
Concurrent TCP stress test for the chat server.

Usage:
  python tests/stress_test.py
  python tests/stress_test.py --clients 50 --concurrency 20 --messages 3

Start the server first with:
  python -m server.main
"""

import argparse
import asyncio
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.protocol import MessageType, decode_messages, encode_message


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("stress_test")


@dataclass
class Stats:
    client_id: int
    connected: bool = False
    registered: bool = False
    logged_in: bool = False
    user_id: Optional[int] = None
    messages_sent: int = 0
    messages_acked: int = 0
    messages_received: int = 0
    errors: list[str] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)


@dataclass
class AggregateReport:
    total_clients: int = 0
    connected: int = 0
    registered: int = 0
    login_success: int = 0
    total_messages_sent: int = 0
    total_messages_acked: int = 0
    total_messages_received: int = 0
    total_errors: int = 0
    avg_latency: float = 0.0
    p50_latency: float = 0.0
    p99_latency: float = 0.0
    max_latency: float = 0.0
    min_latency: float = 0.0
    throughput: float = 0.0
    duration: float = 0.0
    sample_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.connected == self.total_clients
            and self.registered == self.total_clients
            and self.login_success == self.total_clients
            and self.total_errors == 0
            and self.total_messages_sent == self.total_messages_acked
            and self.total_messages_sent == self.total_messages_received
        )


class VirtualClient:
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
        self._username = f"stress_{int(time.time())}_{client_id}_{uuid.uuid4().hex[:6]}"
        self._password = "stress_pass"
        self._buffer = b""
        self._inbox: list[tuple[int, int, dict]] = []
        self._seq = client_id * 100000

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq & 0xFFFFFFFF

    async def run(self) -> Stats:
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
        except Exception as exc:
            self.stats.errors.append(f"unexpected:{type(exc).__name__}:{exc}")
        finally:
            await self._disconnect()
        return self.stats

    async def _connect(self) -> bool:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout,
            )
            self.stats.connected = True
            return True
        except Exception as exc:
            self.stats.errors.append(f"connect_failed:{type(exc).__name__}:{exc}")
            return False

    async def _send(self, msg_type: int, payload: dict, seq: int = None):
        if seq is None:
            seq = self._next_seq()
        self._writer.write(encode_message(msg_type, payload, seq=seq))
        await self._writer.drain()
        return seq

    async def _read_matching(self, expected_type=None, predicate=None, timeout: float = 5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for idx, msg in enumerate(list(self._inbox)):
                msg_type, seq, payload = msg
                if expected_type is not None and msg_type != expected_type:
                    continue
                if predicate is not None and not predicate(seq, payload):
                    continue
                self._inbox.pop(idx)
                return msg

            remaining = max(0.05, deadline - time.time())
            try:
                chunk = await asyncio.wait_for(self._reader.read(4096), timeout=remaining)
            except asyncio.TimeoutError:
                return None
            if not chunk:
                return None

            self._buffer += chunk
            messages, self._buffer = decode_messages(self._buffer)
            for msg_type, seq, payload in messages:
                if msg_type in (MessageType.PRIVATE_MSG, MessageType.GROUP_MSG) and not payload.get("_ack"):
                    self.stats.messages_received += 1
                self._inbox.append((msg_type, seq, payload))

        return None

    async def _send_and_wait(self, msg_type: int, payload: dict, expected_type: int, predicate, timeout=5.0):
        seq = await self._send(msg_type, payload)
        return await self._read_matching(
            expected_type,
            predicate=lambda resp_seq, resp_payload: resp_seq == seq and predicate(resp_payload),
            timeout=timeout,
        )

    async def _register(self) -> bool:
        start = time.time()
        response = await self._send_and_wait(
            MessageType.REGISTER_REQ,
            {"username": self._username, "password_hash": self._password},
            MessageType.REGISTER_RESP,
            lambda payload: payload.get("success") is True,
            timeout=self.timeout,
        )
        if not response:
            self.stats.errors.append("register_failed")
            return False
        self.stats.registered = True
        self.stats.user_id = response[2].get("user_id")
        self.stats.latencies.append(time.time() - start)
        return True

    async def _login(self) -> bool:
        start = time.time()
        response = await self._send_and_wait(
            MessageType.LOGIN_REQ,
            {"username": self._username, "password_hash": self._password},
            MessageType.LOGIN_RESP,
            lambda payload: payload.get("success") is True,
            timeout=self.timeout,
        )
        if not response:
            self.stats.errors.append("login_failed")
            return False
        self.stats.logged_in = True
        self.stats.user_id = response[2].get("user_id")
        self.stats.latencies.append(time.time() - start)
        return True

    async def _exchange_messages(self):
        if not self.stats.user_id:
            self.stats.errors.append("missing_user_id")
            return

        for i in range(self.messages_per_client):
            content = f"stress_msg_{i}_from_{self.client_id}"
            start = time.time()
            response = await self._send_and_wait(
                MessageType.PRIVATE_MSG,
                {
                    "to_id": self.stats.user_id,
                    "content": content,
                    "msg_id": f"stress-{self.client_id}-{i}",
                    "timestamp": int(time.time()),
                },
                MessageType.PRIVATE_MSG,
                lambda payload: payload.get("_ack") is True and payload.get("status") in {"delivered", "stored"},
                timeout=self.timeout,
            )
            self.stats.messages_sent += 1
            if not response:
                self.stats.errors.append(f"message_ack_missing:{i}")
                continue
            self.stats.messages_acked += 1
            self.stats.latencies.append(time.time() - start)

    async def _disconnect(self):
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass


class StressTester:
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
        logger.info(
            "Starting stress test | clients=%d concurrency=%d messages/client=%d",
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

        results = await asyncio.gather(*[_run_client(i) for i in range(self.num_clients)])
        return self._aggregate(results, time.time() - start_time)

    def _aggregate(self, results: list[Stats], duration: float) -> AggregateReport:
        report = AggregateReport(duration=duration)
        latencies: list[float] = []

        for stats in results:
            report.connected += int(stats.connected)
            report.registered += int(stats.registered)
            report.login_success += int(stats.logged_in)
            report.total_messages_sent += stats.messages_sent
            report.total_messages_acked += stats.messages_acked
            report.total_messages_received += stats.messages_received
            report.total_errors += len(stats.errors)
            report.sample_errors.extend(f"client#{stats.client_id}:{err}" for err in stats.errors[:3])
            latencies.extend(stats.latencies)

        report.total_clients = len(results)
        if latencies:
            latencies.sort()
            report.avg_latency = sum(latencies) / len(latencies)
            report.min_latency = latencies[0]
            report.max_latency = latencies[-1]
            report.p50_latency = latencies[len(latencies) // 2]
            report.p99_latency = latencies[min(len(latencies) - 1, int(len(latencies) * 0.99))]
        report.throughput = report.total_messages_acked / duration if duration > 0 else 0.0
        return report


def print_report(report: AggregateReport):
    separator = "=" * 60
    print(f"\n{separator}")
    print("  STRESS TEST REPORT")
    print(separator)
    print(f"  Duration:              {report.duration:.2f} s")
    print(f"  Total Clients:         {report.total_clients}")
    print(f"  Connected:             {report.connected}")
    print(f"  Registered:            {report.registered}")
    print(f"  Login Success:         {report.login_success}")
    print(f"  Messages Sent:         {report.total_messages_sent}")
    print(f"  Messages ACKed:        {report.total_messages_acked}")
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
    print(f"  Throughput:            {report.throughput:.2f} acked msg/s")
    print(separator)

    if report.total_errors:
        print("  Sample errors:")
        for err in report.sample_errors[:10]:
            print(f"    - {err}")
        print(separator)

    print(f"  Result:                {'PASS' if report.ok else 'FAIL'}")
    print(separator)


def parse_args():
    parser = argparse.ArgumentParser(description="Chat server stress test")
    parser.add_argument("--host", default="127.0.0.1", help="server host")
    parser.add_argument("--port", type=int, default=8888, help="server TCP port")
    parser.add_argument("--clients", type=int, default=10, help="virtual client count")
    parser.add_argument("--concurrency", type=int, default=10, help="concurrent clients")
    parser.add_argument("--messages", type=int, default=5, help="messages per client")
    parser.add_argument("--timeout", type=float, default=10.0, help="operation timeout seconds")
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
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
