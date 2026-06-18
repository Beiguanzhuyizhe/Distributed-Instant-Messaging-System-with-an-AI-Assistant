import pytest

from server.config import ServerConfig
from server.protocol import MessageType
from server.tcp_server import ChatServer, ConnectionManager
import server.ai_service as ai_service_module
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
async def test_private_content_warning_is_scoped_to_target_chat(server):
    conn = DummyConnection()
    conn_id = await server.conn_manager.add(conn)
    await server.conn_manager.bind_user(conn_id, 1)
    server.msg_router._moderate = lambda content: {
        "rejected": True,
        "level": "high",
        "clean_content": content,
    }

    result = await server.msg_router.route_private_msg(1, 2, "blocked")

    assert result["status"] == "rejected"
    msg_type, seq, payload = conn.sent[-1]
    assert msg_type == MessageType.CONTENT_WARN
    assert seq is None
    assert payload["related_type"] == "private"
    assert payload["related_target"] == "2"
    assert payload["chat_key"] == "private:2"


@pytest.mark.asyncio
async def test_group_content_warning_is_scoped_to_group_chat(server):
    conn = DummyConnection()
    conn_id = await server.conn_manager.add(conn)
    await server.conn_manager.bind_user(conn_id, 1)
    server.msg_router._moderate = lambda content: {
        "rejected": True,
        "level": "high",
        "clean_content": content,
    }

    result = await server.msg_router.route_group_msg(1, 9, "blocked")

    assert result["status"] == "rejected"
    msg_type, seq, payload = conn.sent[-1]
    assert msg_type == MessageType.CONTENT_WARN
    assert seq is None
    assert payload["related_type"] == "group"
    assert payload["related_target"] == "9"
    assert payload["chat_key"] == "group:9"
    assert payload["group_id"] == "9"


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
async def test_group_file_completion_is_broadcast_with_group_context(server):
    calls = []

    async def fake_send_to_group(group_id, msg_type, payload, exclude_user_id=None):
        calls.append((group_id, msg_type, payload, exclude_user_id))

    server.msg_router.send_to_group = fake_send_to_group

    await server._notify_file_completed("file-g", {
        "sender_id": 1,
        "receiver_id": None,
        "group_id": 9,
        "filename": "group.txt",
        "filesize": 4,
    })

    assert len(calls) == 1
    group_id, msg_type, payload, exclude_user_id = calls[0]
    assert group_id == 9
    assert msg_type == MessageType.FILE_INIT
    assert exclude_user_id == 1
    assert payload["chat_key"] == "group:9"
    assert payload["related_type"] == "group"
    assert payload["related_target"] == "9"
    assert payload["from_id"] == 1


@pytest.mark.asyncio
async def test_file_ack_failure_echoes_file_id_and_offset(server):
    async def fake_get_chunk(file_id, offset, requester_id=None):
        assert file_id == "file-1"
        assert offset == 65536
        assert requester_id == 2
        return {"success": False, "error": "permission_denied"}

    conn = DummyConnection()
    conn_id = await server.conn_manager.add(conn)
    await server.conn_manager.bind_user(conn_id, 2)
    server.file_transfer.get_chunk = fake_get_chunk

    await server._handle_file_ack(
        conn_id, 30, {"file_id": "file-1", "offset": 65536}
    )

    msg_type, seq, payload = conn.sent[-1]
    assert msg_type == MessageType.FILE_ACK
    assert seq == 30
    assert payload["success"] is False
    assert payload["error"] == "permission_denied"
    assert payload["file_id"] == "file-1"
    assert payload["offset"] == 65536


@pytest.mark.asyncio
async def test_login_response_includes_user_and_available_groups(server):
    conn = DummyConnection()
    conn_id = await server.conn_manager.add(conn)
    server.user_manager.login = _async_return({
        "success": True,
        "user_id": 1,
    })
    server.group_manager.get_user_groups = _async_return([
        {"id": 2, "name": "demo_group"},
    ])
    server.group_manager.get_all_groups = _async_return([
        {"id": 2, "name": "demo_group", "member_count": 1},
        {"id": 3, "name": "other_group", "member_count": 0},
    ])
    calls = []
    server.msg_router.broadcast_online_status = _record_async(calls, "broadcast")

    await server._handle_login(conn_id, 20, {
        "username": "alice",
        "password_hash": "pw",
    })

    msg_type, seq, payload = conn.sent[-1]
    assert msg_type == MessageType.LOGIN_RESP
    assert seq == 20
    assert payload["groups"] == {"2": "demo_group"}
    assert payload["available_groups"]["2"]["joined"] is True
    assert payload["available_groups"]["3"]["joined"] is False


@pytest.mark.asyncio
async def test_online_users_response_includes_group_state(server):
    conn = DummyConnection()
    conn_id = await server.conn_manager.add(conn)
    await server.conn_manager.bind_user(conn_id, 1)
    server.user_manager.get_online_users = _async_return([
        {"id": 1, "username": "alice"},
    ])
    server.group_manager.get_user_groups = _async_return([
        {"id": 2, "name": "demo_group"},
    ])
    server.group_manager.get_all_groups = _async_return([
        {"id": 2, "name": "demo_group", "member_count": 1},
    ])

    await server._handle_online_users(conn_id, 21)

    msg_type, seq, payload = conn.sent[-1]
    assert msg_type == MessageType.ONLINE_USERS
    assert seq == 21
    assert payload["groups"] == {"2": "demo_group"}
    assert payload["available_groups"]["2"]["name"] == "demo_group"


@pytest.mark.asyncio
async def test_group_ai_reply_strips_member_name_prefix(monkeypatch, server):
    class FakeAI:
        available = True

        async def query_with_context(self, query, username="", history=None):
            self.query = query
            self.username = username
            self.history = history or []
            return "bob: 这是群内可见的回答"

    fake_ai = FakeAI()
    monkeypatch.setattr(ai_service_module, "get_ai_service", lambda _config: fake_ai)

    conn = DummyConnection()
    conn_id = await server.conn_manager.add(conn)
    await server.conn_manager.bind_user(conn_id, 1)
    server.user_manager.get_user_info = _user_info({
        1: "alice",
        2: "bob",
    })
    server.group_manager.is_member = _async_return(True)
    server.group_manager.get_group_members = _async_return([
        {"id": 1, "username": "alice"},
        {"id": 2, "username": "bob"},
    ])
    server.msg_history.get_group_history = _async_return([
        {"sender_id": 2, "content": "之前的问题", "recalled": 0},
    ])
    broadcasts = []

    async def fake_send_to_group(group_id, msg_type, payload, exclude_user_id=None):
        broadcasts.append((group_id, msg_type, payload, exclude_user_id))

    server.msg_router.send_to_group = fake_send_to_group

    await server._handle_ai_query(conn_id, 21, {
        "query": "@ai 帮我总结",
        "group_id": 9,
    })

    msg_type, seq, payload = conn.sent[-1]
    assert msg_type == MessageType.AI_RESP
    assert seq == 21
    assert payload["content"] == "这是群内可见的回答"
    assert payload["chat_key"] == "group:9"
    assert broadcasts[0][2]["content"] == "这是群内可见的回答"
    assert all("bob:" not in item["content"] for item in fake_ai.history)


@pytest.mark.asyncio
async def test_direct_ai_reply_strips_requester_name_prefix(monkeypatch, server):
    class FakeAI:
        available = True

        async def query_with_context(self, query, username="", history=None):
            return "alice: 这是独立 AI 对话回答"

    monkeypatch.setattr(ai_service_module, "get_ai_service", lambda _config: FakeAI())

    conn = DummyConnection()
    conn_id = await server.conn_manager.add(conn)
    await server.conn_manager.bind_user(conn_id, 1)
    server.user_manager.get_user_info = _user_info({
        1: "alice",
    })

    await server._handle_ai_query(conn_id, 22, {
        "query": "hello",
    })

    msg_type, seq, payload = conn.sent[-1]
    assert msg_type == MessageType.AI_RESP
    assert seq == 22
    assert payload["content"] == "这是独立 AI 对话回答"
    assert payload.get("group_id") is None


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


def _user_info(names):
    async def inner(user_id):
        username = names.get(user_id)
        if username is None:
            return None
        return {"id": user_id, "username": username}
    return inner
