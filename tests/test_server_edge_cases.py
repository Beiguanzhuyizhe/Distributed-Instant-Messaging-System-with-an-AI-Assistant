import pytest

from server.config import ServerConfig
from server.protocol import MessageType
from server.tcp_server import ChatServer, ConnectionManager
from tests.temp_utils import make_runtime_dir, remove_runtime_dir


class DummyConnection:
    """最小连接桩：记录发送内容并暴露 close/wait_closed 状态。"""

    def __init__(self):
        self.sent = []
        self.is_closed = False
        self.waited = False
        self.remote_addr = ("127.0.0.1", 12345)

    async def send_message(self, msg_type, payload, seq=None):
        self.sent.append((msg_type, seq, payload))

    def close(self):
        self.is_closed = True

    async def wait_closed(self):
        self.waited = True


@pytest.fixture
def server():
    runtime_dir = make_runtime_dir("server_edge_")
    srv = ChatServer(ServerConfig(
        db_path=str(runtime_dir / "chat.db"),
        file_storage_dir=str(runtime_dir / "files"),
    ))
    yield srv
    remove_runtime_dir(runtime_dir)


@pytest.mark.asyncio
async def test_connection_capacity_is_enforced_atomically():
    manager = ConnectionManager()
    first = await manager.add(DummyConnection(), max_connections=1)
    second = await manager.add(DummyConnection(), max_connections=1)

    assert first == 1
    assert second is None
    assert manager.active_count == 1


@pytest.mark.asyncio
async def test_duplicate_login_fully_closes_old_connection():
    manager = ConnectionManager()
    old = DummyConnection()
    new = DummyConnection()
    old_id = await manager.add(old)
    new_id = await manager.add(new)
    await manager.bind_user(old_id, 7)

    await manager.bind_user(new_id, 7)

    assert old.is_closed
    assert old.waited
    assert manager.get_by_user(7) is new


@pytest.mark.asyncio
async def test_private_message_to_self_is_rejected(server):
    conn = DummyConnection()
    conn_id = await server.conn_manager.add(conn)
    await server.conn_manager.bind_user(conn_id, 1)

    await server._handle_private_msg(
        conn_id, 10, {"to_id": 1, "content": "self"}
    )

    msg_type, seq, payload = conn.sent[-1]
    assert msg_type == MessageType.PRIVATE_MSG
    assert seq == 10
    assert payload["_ack"] is True
    assert payload["status"] == "rejected"
    assert payload["msg_id"] == ""


@pytest.mark.asyncio
async def test_group_nonmember_gets_rejected_ack(server):
    conn = DummyConnection()
    conn_id = await server.conn_manager.add(conn)
    await server.conn_manager.bind_user(conn_id, 1)
    server.group_manager.is_member = _async_return(False)

    await server._handle_group_msg(
        conn_id, 11, {"group_id": 2, "content": "hello"}
    )

    msg_type, seq, payload = conn.sent[-1]
    assert msg_type == MessageType.GROUP_MSG
    assert seq == 11
    assert payload["_ack"] is True
    assert payload["status"] == "rejected"


@pytest.mark.asyncio
async def test_history_rejects_self_private_chat(server):
    conn = DummyConnection()
    conn_id = await server.conn_manager.add(conn)
    await server.conn_manager.bind_user(conn_id, 1)

    await server._handle_history_req(
        conn_id, 12, {"type": "private", "target_id": 1}
    )

    assert conn.sent[-1][0] == MessageType.ERROR
    assert "自己" in conn.sent[-1][2]["message"]


@pytest.mark.asyncio
async def test_non_object_payload_is_rejected(server):
    conn = DummyConnection()
    conn_id = await server.conn_manager.add(conn)

    await server._dispatch(conn_id, MessageType.LOGIN_REQ, 13, ["bad"])

    assert conn.sent[-1][0] == MessageType.ERROR
    assert "格式" in conn.sent[-1][2]["message"]


@pytest.mark.asyncio
async def test_old_connection_cleanup_does_not_mark_replacement_offline(server):
    old = DummyConnection()
    new = DummyConnection()
    old_id = await server.conn_manager.add(old)
    new_id = await server.conn_manager.add(new)
    await server.conn_manager.bind_user(old_id, 7)

    original_get_user_id = server.conn_manager.get_user_id
    server.conn_manager.get_user_id = lambda conn_id: (
        7 if conn_id == old_id else original_get_user_id(conn_id)
    )
    await server.conn_manager.bind_user(new_id, 7)
    calls = []
    server.user_manager.set_online_status = _record_async(calls, "set_online")
    server.msg_router.broadcast_online_status = _record_async(calls, "broadcast")

    await server._cleanup_connection(old_id)

    assert calls == []
    assert server.conn_manager.get_by_user(7) is new


def _async_return(value):
    async def inner(*_args, **_kwargs):
        return value
    return inner


def _record_async(calls, name):
    async def inner(*args, **_kwargs):
        calls.append((name, args))
    return inner
