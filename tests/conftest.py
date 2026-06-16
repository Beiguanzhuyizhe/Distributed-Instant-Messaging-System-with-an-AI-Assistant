"""
pytest 配置文件与 fixtures
"""

import asyncio
import sys
from pathlib import Path

import pytest

# 确保可以导入 server 模块
SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

CLIENT_DIR = Path(__file__).resolve().parent.parent / "client"
if str(CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENT_DIR))


# ── 通用 fixtures ──────────────────────────────────────────────────


@pytest.fixture(scope="session")
def event_loop():
    """为 session 级异步 fixture 提供事件循环。"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def server_protocol():
    from server import protocol

    return protocol


@pytest.fixture
def client_protocol():
    from client import protocol

    return protocol


@pytest.fixture
def protocol_module():
    """延迟导入 protocol 模块，确保 sys.path 已修正。"""
    import protocol as p

    return p


# ── 模拟数据 fixtures ──────────────────────────────────────────────


@pytest.fixture
def sample_login_payload(server_protocol):
    return server_protocol.make_login_payload("testuser", "password123")


@pytest.fixture
def sample_register_payload(server_protocol):
    return server_protocol.make_register_payload(
        "newuser", "pass456", public_key="abc123pubkey"
    )


@pytest.fixture
def sample_private_msg_payload(server_protocol):
    return server_protocol.make_private_msg_payload(
        1, 2, "Hello Bob!",
        msg_id=1001, timestamp=1700000000,
    )


@pytest.fixture
def sample_group_msg_payload(server_protocol):
    return server_protocol.make_group_msg_payload(
        1, 1, "Hello everyone!",
        msg_id=2001, timestamp=1700000000,
    )


@pytest.fixture
def sample_error_payload(server_protocol):
    return server_protocol.make_error_payload(2, "Auth failed")


@pytest.fixture
def sample_large_payload():
    """生成一个接近 1MB 的大 payload 用于边界测试。"""
    return {"data": "x" * 900_000}


# ── 模拟 TCP 流 ────────────────────────────────────────────────────


class MockStreamReader:
    """模拟 asyncio.StreamReader 用于测试。"""

    def __init__(self, data: bytes = b""):
        self._buffer = data
        self._offset = 0

    def feed_data(self, data: bytes):
        self._buffer += data

    async def read(self, n: int = -1):
        if n == -1:
            remaining = self._buffer[self._offset :]
            self._offset = len(self._buffer)
            return remaining
        if self._offset >= len(self._buffer):
            return b""
        avail = min(n, len(self._buffer) - self._offset)
        chunk = self._buffer[self._offset : self._offset + avail]
        self._offset += avail
        return chunk


class MockStreamWriter:
    """模拟 asyncio.StreamWriter 用于测试。"""

    def __init__(self):
        self.buffer = b""

    def write(self, data: bytes):
        self.buffer += data

    def close(self):
        pass

    def get_extra_info(self, name, default=None):
        if name == "peername":
            return ("127.0.0.1", 54321)
        if name == "sockname":
            return ("127.0.0.1", 8888)
        return default


@pytest.fixture
def mock_stream_reader():
    return MockStreamReader()


@pytest.fixture
def mock_stream_writer():
    return MockStreamWriter()
