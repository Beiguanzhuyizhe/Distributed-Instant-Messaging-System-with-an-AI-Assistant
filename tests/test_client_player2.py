"""
Player2 客户端功能回归测试。

这些测试聚焦 CLI/GUI 共用的客户端行为：撤回、历史、文件传输和 ACK 对齐。
测试不启动真实服务器，而是用假连接/假 handler 捕获客户端发出的协议 payload。
"""

from client.message_handler import MessageHandler
from client.protocol import MessageType
from client.cli import ChatCLI
from client.gui import ChatGUI
import client.web_bridge as web_bridge_module
from client.web_bridge import WebBridge
from tests.temp_utils import make_runtime_dir, remove_runtime_dir


class DummyConnection:
    """记录 MessageHandler 发出的消息，避免依赖真实 TCP 连接。"""

    def __init__(self):
        self.callbacks = {}
        self.sent = []

    def register_callback(self, msg_type, callback):
        self.callbacks[msg_type] = callback

    def send_message(self, msg_type, payload, seq=None):
        self.sent.append({"msg_type": msg_type, "payload": payload, "seq": seq})
        return True

    @property
    def is_connected(self):
        return True


class OfflineConnection(DummyConnection):
    @property
    def is_connected(self):
        return False


class DummyHandler:
    """记录 CLI/GUI 调用的高层发送接口。"""

    def __init__(self):
        self.calls = []
        self._seq = 0

    def request_history(self, target_type, target_id, limit=50):
        self.calls.append(("history", target_type, target_id, limit))

    def request_online_users(self):
        self.calls.append(("online_users",))

    def send_private_msg(self, from_id, to_id, content):
        self._seq += 1
        self.calls.append(("private", from_id, to_id, content))
        return {"ok": True, "seq": self._seq, "client_msg_id": f"local-{self._seq}"}

    def send_group_msg(self, from_id, group_id, content):
        self._seq += 1
        self.calls.append(("group", from_id, group_id, content))
        return {"ok": True, "seq": self._seq, "client_msg_id": f"local-{self._seq}"}

    def send_recall(self, msg_id):
        self.calls.append(("recall", msg_id))

    def send_file_init(self, from_id, to_id, filename, filesize, file_id, group_id=None):
        self.calls.append(("file_init", from_id, to_id, filename, filesize, file_id, group_id))
        return {"ok": True, "seq": 1, "client_file_id": file_id}

    def send_file_data(self, file_id, chunk_data, chunk_index, total_chunks):
        self.calls.append(("file_data", file_id, chunk_index, total_chunks, chunk_data))
        return {"ok": True}

    def send_ai_query(self, from_id, group_id, query, context=None):
        self._seq += 1
        self.calls.append(("ai", from_id, group_id, query, context))
        return {"ok": True, "seq": self._seq}

    def group_create(self, name, user_id):
        self._seq += 1
        self.calls.append(("create", name, user_id))
        return {"ok": True, "seq": self._seq}

    def group_join(self, group_id, user_id):
        self._seq += 1
        self.calls.append(("join", group_id, user_id))
        return {"ok": True, "seq": self._seq}

    def group_leave(self, group_id, user_id):
        self._seq += 1
        self.calls.append(("leave", group_id, user_id))
        return {"ok": True, "seq": self._seq}


class DummyStore:
    def __init__(self):
        self.status_updates = []
        self.id_updates = []

    def update_message_status(self, username, msg_id, status):
        self.status_updates.append((username, msg_id, status))
        return True

    def update_message_id(self, username, local_msg_id, server_msg_id, timestamp=None, status=""):
        self.id_updates.append((username, local_msg_id, server_msg_id, timestamp, status))
        return True


def test_message_handler_returns_tracking_info_for_private_message():
    conn = DummyConnection()
    handler = MessageHandler(conn)

    result = handler.send_private_msg(1, 2, "hello")

    assert result["ok"] is True
    assert result["seq"] == conn.sent[-1]["seq"]
    assert result["payload"] == conn.sent[-1]["payload"]
    assert result["client_msg_id"] == conn.sent[-1]["payload"]["msg_id"]
    assert conn.sent[-1]["msg_type"] == MessageType.PRIVATE_MSG


def test_message_handler_file_init_supports_group_id():
    conn = DummyConnection()
    handler = MessageHandler(conn)

    result = handler.send_file_init(1, None, "group.txt", 4, "file-g", group_id=9)

    assert result["ok"] is True
    payload = conn.sent[-1]["payload"]
    assert payload["group_id"] == 9
    assert "to_id" not in payload
    assert payload["file_id"] == "file-g"


def test_cli_recall_accepts_server_uuid_msg_id():
    cli = ChatCLI.__new__(ChatCLI)
    cli.handler = DummyHandler()
    cli._print = lambda *args, **kwargs: None

    cli._handle_command("/recall 6f1b1e8d-9c84-4e6f-9d8c-123456789abc")

    assert cli.handler.calls == [
        ("recall", "6f1b1e8d-9c84-4e6f-9d8c-123456789abc")
    ]


def test_cli_private_history_resolves_username_to_user_id():
    cli = ChatCLI.__new__(ChatCLI)
    cli.handler = DummyHandler()
    cli._print = lambda *args, **kwargs: None
    cli._online_users = {"alice": 2}
    cli._chat_type = "private"

    cli._handle_command("/history alice")

    assert cli.handler.calls == [("history", "private", 2, 50)]


def test_cli_ack_updates_pending_message_to_server_uuid():
    cli = ChatCLI.__new__(ChatCLI)
    cli._pending_acks = {}
    cli._last_sent_msg_id = None
    cli._username = None
    cli._print = lambda *args, **kwargs: None
    msg = {"local_msg_id": "101", "msg_id": "101", "status": "pending"}
    cli._pending_acks[7] = msg

    cli._apply_message_ack(7, {
        "msg_id": "6f1b1e8d-9c84-4e6f-9d8c-123456789abc",
        "timestamp": 1700000000,
        "status": "delivered",
    })

    assert msg["msg_id"] == "6f1b1e8d-9c84-4e6f-9d8c-123456789abc"
    assert msg["server_msg_id"] == "6f1b1e8d-9c84-4e6f-9d8c-123456789abc"
    assert msg["status"] == "delivered"
    assert cli._last_sent_msg_id == "6f1b1e8d-9c84-4e6f-9d8c-123456789abc"
    assert cli._pending_acks == {}


def test_cli_ack_without_server_msg_id_does_not_create_recallable_id():
    cli = ChatCLI.__new__(ChatCLI)
    cli._pending_acks = {}
    cli._last_sent_msg_id = None
    cli._username = None
    msg = {"local_msg_id": "101", "msg_id": "101", "status": "pending"}
    cli._pending_acks[7] = msg

    cli._apply_message_ack(7, {"msg_id": "", "timestamp": 0, "status": "rejected"})

    assert msg["msg_id"] == "101"
    assert msg["status"] == "rejected"
    assert "server_msg_id" not in msg
    assert cli._last_sent_msg_id is None
    assert cli._pending_acks == {}


def test_cli_send_file_uses_string_file_id():
    tmp_dir = make_runtime_dir("client_player2_")
    try:
        sample = tmp_dir / "now.md"
        sample.write_text("demo", encoding="utf-8")

        cli = ChatCLI.__new__(ChatCLI)
        cli.handler = DummyHandler()
        cli._print = lambda *args, **kwargs: None
        cli._online_users = {"bob": 2}
        cli._user_id = 1

        cli._send_file("bob", str(sample))

        init_call = cli.handler.calls[0]
        data_call = cli.handler.calls[1]
        assert init_call[0] == "file_init"
        assert isinstance(init_call[5], str)
        assert data_call[1] == init_call[5]
    finally:
        remove_runtime_dir(tmp_dir)


def test_gui_private_history_resolves_selected_user_id():
    gui = ChatGUI()
    gui.handler = DummyHandler()
    gui._current_target = "alice"
    gui._current_target_id = 2
    gui._chat_type = "private"

    gui._menu_history()

    assert gui.handler.calls == [("history", "private", 2, 50)]


def test_cli_private_messages_use_stable_peer_context():
    cli = ChatCLI.__new__(ChatCLI)
    cli.handler = DummyHandler()
    cli._print = lambda *args, **kwargs: None
    cli._online_users = {"bob": 2, "carol": 3}
    cli._user_id = 1
    cli._username = "alice"
    captured = []
    cli._append_and_store = captured.append
    cli._pending_acks = {}

    cli._send_private("bob", "hello bob")
    cli._send_private("carol", "hello carol")

    assert [m["chat_key"] for m in captured] == ["private:2", "private:3"]
    assert [m["related_target"] for m in captured] == ["2", "3"]
    assert captured[0]["target_id"] == 2
    assert captured[1]["target_id"] == 3


def test_cli_incoming_private_message_is_bound_to_sender_peer():
    cli = ChatCLI.__new__(ChatCLI)
    cli._user_id = 1
    cli._username = "alice"
    cli._print = lambda *args, **kwargs: None
    captured = []
    cli._append_and_store = captured.append

    cli._on_private_msg(MessageType.PRIVATE_MSG, 1, {
        "from_id": 2,
        "to_id": 1,
        "from_username": "bob",
        "content": "from bob",
        "msg_id": "server-1",
        "timestamp": 1700000000,
    })

    assert captured == [{
        "type": "private",
        "sender": "bob",
        "receiver_id": 1,
        "content": "from bob",
        "msg_id": "server-1",
        "timestamp": 1700000000,
        "from_id": 2,
        "target_id": 2,
        "related_type": "private",
        "related_target": "2",
        "chat_key": "private:2",
    }]


def test_web_bridge_private_messages_use_stable_peer_context():
    bridge = WebBridge.__new__(WebBridge)
    bridge.conn = DummyConnection()
    bridge.handler = DummyHandler()
    bridge._user_id = 1
    bridge._username = "alice"
    bridge._messages = []
    bridge._pending_acks = {}
    captured = []
    bridge._append_and_store = captured.append
    bridge._remember_pending = lambda result, msg: None
    bridge._push_msg = lambda msg: None

    result = bridge.send_private_msg(3, "hello carol")

    assert result["ok"] is True
    assert captured[0]["chat_key"] == "private:3"
    assert captured[0]["related_target"] == "3"
    assert captured[0]["receiver_id"] == 3


def test_web_bridge_login_restores_groups_and_available_groups():
    bridge = WebBridge.__new__(WebBridge)
    bridge.handler = DummyHandler()
    bridge._username = "alice"
    bridge._user_id = None
    bridge._logged_in = False
    bridge._online_users = {}
    bridge._groups = {}
    bridge._available_groups = {}
    events = []
    bridge._push = lambda event_type, data: events.append((event_type, data))

    bridge._on_login_resp(MessageType.LOGIN_RESP, 1, {
        "success": True,
        "user_id": 1,
        "username": "alice",
        "groups": {"2": "demo_group"},
        "available_groups": {
            "2": {"id": 2, "name": "demo_group", "joined": True},
            "3": {"id": 3, "name": "other_group", "joined": False},
        },
    })

    assert bridge._groups == {"2": "demo_group"}
    assert bridge._available_groups["3"]["name"] == "other_group"
    assert events[-1][0] == "login_success"
    assert events[-1][1]["groups"] == {"2": "demo_group"}
    assert "available_groups" in events[-1][1]
    assert ("online_users",) in bridge.handler.calls


def test_web_bridge_offline_send_reports_system_message():
    bridge = WebBridge.__new__(WebBridge)
    bridge.conn = OfflineConnection()
    bridge.handler = DummyHandler()
    bridge._user_id = 1
    bridge._username = "alice"
    bridge._chat_type = "private"
    bridge._current_target = "bob"
    bridge._current_target_id = 2
    events = []
    bridge._push_msg = lambda msg: events.append(("new_message", msg))
    bridge._push = lambda event_type, data: events.append((event_type, data))

    result = bridge.send_private_msg(2, "offline")

    assert result == {"ok": False, "error": "Disconnected"}
    assert ("private", 1, 2, "offline") not in bridge.handler.calls
    assert any("Cannot send while disconnected" in event[1].get("content", "") for event in events if event[0] == "new_message")
    assert ("connection_status", {"status": "disconnected"}) in events


def test_web_bridge_history_formats_private_messages_for_requested_peer():
    bridge = WebBridge.__new__(WebBridge)
    bridge._user_id = 1
    bridge._username = "alice"
    bridge._online_users = {"alice": 1, "bob": 2, "carol": 3}
    bridge._messages = []
    pushed = []
    bridge._push = lambda event_type, data: pushed.append((event_type, data))

    bridge._on_history(MessageType.HISTORY_RESP, 10, {
        "type": "private",
        "target_id": 2,
        "messages": [
            {
                "msg_id": "m1",
                "sender_id": 1,
                "receiver_id": 2,
                "content": "to bob",
                "created_at": 1700000000.0,
            },
            {
                "msg_id": "m2",
                "sender_id": 2,
                "receiver_id": 1,
                "content": "from bob",
                "created_at": 1700000001.0,
            },
        ],
    })

    assert pushed[0][0] == "history"
    data = pushed[0][1]
    assert data["target_id"] == 2
    assert [m["chat_key"] for m in data["messages"]] == ["private:2", "private:2"]
    assert [m["related_target"] for m in data["messages"]] == ["2", "2"]


def test_web_bridge_rejected_ack_updates_pending_status():
    bridge = WebBridge.__new__(WebBridge)
    bridge._pending_acks = {
        7: {
            "local_msg_id": "local-7",
            "msg_id": "local-7",
            "status": "pending",
        }
    }
    bridge._username = "alice"
    bridge._chat_type = "private"
    bridge._current_target_id = 2
    bridge._current_target = "bob"
    bridge.store = DummyStore()
    events = []
    bridge._push = lambda event_type, data: events.append((event_type, data))
    bridge._push_msg = lambda msg: events.append(("new_message", msg))

    bridge._apply_message_ack(7, {
        "msg_id": "",
        "status": "rejected",
        "error": "不能给自己发送私聊",
    })

    assert bridge._pending_acks == {}
    assert bridge.store.status_updates == [("alice", "local-7", "rejected")]
    assert events[-2][0] == "new_message"
    assert "不能给自己发送私聊" in events[-2][1]["content"]
    assert events[-1] == (
        "message_acked",
        {
            "local_msg_id": "local-7",
            "msg_id": "local-7",
            "timestamp": events[-1][1]["timestamp"],
            "status": "rejected",
            "error": "不能给自己发送私聊",
        },
    )


def test_web_bridge_rejected_ack_uses_original_chat_context():
    bridge = WebBridge.__new__(WebBridge)
    bridge._pending_acks = {
        7: {
            "local_msg_id": "local-7",
            "msg_id": "local-7",
            "status": "pending",
            "related_type": "private",
            "related_target": "2",
            "chat_key": "private:2",
        }
    }
    bridge._username = "alice"
    bridge._chat_type = "private"
    bridge._current_target_id = 3
    bridge._current_target = "carol"
    bridge._chat_key = lambda chat_type, target: f"{chat_type}:{target}"
    bridge.store = DummyStore()
    events = []
    bridge._push = lambda event_type, data: events.append((event_type, data))
    bridge._push_msg = lambda msg: events.append(("new_message", msg))

    bridge._apply_message_ack(7, {
        "msg_id": "",
        "status": "rejected",
        "error": "not member",
    })

    system_event = events[-2][1]
    assert system_event["chat_key"] == "private:2"
    assert system_event["related_target"] == "2"


def test_web_bridge_content_warn_uses_payload_context():
    bridge = WebBridge.__new__(WebBridge)
    bridge._chat_type = "private"
    bridge._current_target_id = 3
    bridge._current_target = "carol"
    bridge._chat_key = lambda chat_type, target: f"{chat_type}:{target}"
    events = []
    bridge._push = lambda event_type, data: events.append((event_type, data))

    bridge._on_content_warn(MessageType.CONTENT_WARN, 1, {
        "message": "blocked",
        "related_type": "private",
        "related_target": "2",
        "chat_key": "private:2",
    })

    assert events[-1][0] == "new_message"
    msg = events[-1][1]
    assert msg["chat_key"] == "private:2"
    assert msg["related_target"] == "2"
    assert msg["event_id"].startswith("evt-")


def test_web_bridge_error_uses_payload_context():
    bridge = WebBridge.__new__(WebBridge)
    bridge._chat_type = "private"
    bridge._current_target_id = 3
    bridge._current_target = "carol"
    bridge._chat_key = lambda chat_type, target: f"{chat_type}:{target}"
    events = []
    bridge._push = lambda event_type, data: events.append((event_type, data))

    bridge._on_error(MessageType.ERROR, 1, {
        "code": 1,
        "message": "not member",
        "related_type": "group",
        "related_target": "9",
        "chat_key": "group:9",
        "group_id": "9",
    })

    assert events[-1][0] == "new_message"
    msg = events[-1][1]
    assert msg["chat_key"] == "group:9"
    assert msg["related_type"] == "group"
    assert msg["group_id"] == "9"


def test_web_bridge_ai_context_is_correlated_by_sequence():
    bridge = WebBridge.__new__(WebBridge)
    bridge.handler = DummyHandler()
    bridge.store = DummyStore()
    bridge._pending_ai_context = {}
    bridge._username = "alice"
    bridge._user_id = 1
    bridge._chat_type = "private"
    bridge._current_target = "bob"
    bridge._current_target_id = 2
    bridge._messages = []
    events = []
    bridge._push_msg = lambda msg: events.append(msg)
    bridge._append_and_store = lambda msg: bridge._messages.append(msg)

    bridge.send_ai_query("first")
    first_seq = bridge.handler._seq
    bridge._current_target = "carol"
    bridge._current_target_id = 3
    bridge.send_ai_query("second")
    second_seq = bridge.handler._seq

    bridge._on_ai_resp(MessageType.AI_RESP, second_seq, {"content": "second reply"})
    bridge._on_ai_resp(MessageType.AI_RESP, first_seq, {"content": "first reply"})

    assert [m["chat_key"] for m in bridge._messages] == ["private:3", "private:2"]
    assert [m["related_target"] for m in bridge._messages] == ["3", "2"]


def test_web_bridge_group_ai_broadcast_uses_group_context():
    bridge = WebBridge.__new__(WebBridge)
    bridge.store = DummyStore()
    bridge._messages = []
    bridge._append_and_store = lambda msg: bridge._messages.append(msg)
    bridge._push_msg = lambda msg: None

    bridge._on_ai_resp(MessageType.AI_RESP, None, {
        "content": "group reply",
        "group_id": 9,
    })

    assert bridge._messages[0]["chat_key"] == "group:9"
    assert bridge._messages[0]["related_type"] == "group"
    assert bridge._messages[0]["related_target"] == "9"


def test_web_bridge_demo_ai_query_adds_visible_user_message_for_ai_chat():
    bridge = WebBridge.__new__(WebBridge)
    bridge.handler = DummyHandler()
    bridge.store = DummyStore()
    bridge._messages = [{
        "type": "ai",
        "sender": "AI Assistant",
        "content": "earlier reply",
        "chat_key": "ai:AI Assistant",
    }]
    bridge._pending_ai_context = {}
    bridge._username = "alice"
    bridge._user_id = 1
    bridge._chat_type = "ai"
    bridge._current_target = "AI Assistant"
    bridge._current_target_id = -1
    pushed = []
    bridge._append_and_store = lambda msg: bridge._messages.append(msg)
    bridge._push_msg = lambda msg: pushed.append(msg)

    result = bridge.demo_send_ai_query("hello AI")

    assert result["ok"] is True
    assert bridge.handler.calls[-1][0] == "ai"
    assert bridge.handler.calls[-1][4] == [{"role": "assistant", "content": "earlier reply"}]
    assert bridge._messages[-1]["type"] == "private"
    assert bridge._messages[-1]["sender"] == "alice"
    assert bridge._messages[-1]["receiver"] == "AI Assistant"
    assert bridge._messages[-1]["chat_key"] == "ai:AI Assistant"
    assert pushed[-1]["content"] == "hello AI"


def test_web_bridge_demo_group_ai_query_uses_explicit_group_context():
    bridge = WebBridge.__new__(WebBridge)
    bridge.handler = DummyHandler()
    bridge.store = DummyStore()
    bridge._messages = []
    bridge._pending_ai_context = {}
    bridge._username = "alice"
    bridge._user_id = 1
    bridge._chat_type = "private"
    bridge._current_target = "bob"
    bridge._current_target_id = 2
    pushed = []
    bridge._push_msg = lambda msg: pushed.append(msg)

    result = bridge.demo_send_ai_query("group hello", 9, chat_type="group", target_id="9")

    assert result["ok"] is True
    assert bridge.handler.calls[-1] == ("ai", 1, 9, "group hello", [])
    assert pushed[-1]["related_type"] == "group"
    assert pushed[-1]["related_target"] == "9"
    assert pushed[-1]["chat_key"] == "group:9"
    assert pushed[-1]["group_id"] == "9"


def test_web_bridge_leave_group_removes_sidebar_entry_without_payload_group_id():
    bridge = WebBridge.__new__(WebBridge)
    bridge.handler = DummyHandler()
    bridge._user_id = 1
    bridge._groups = {"9": "demo"}
    bridge._pending_group_leave = {}
    events = []
    bridge._push = lambda event_type, data: events.append((event_type, data))
    bridge._push_msg = lambda msg: events.append(("new_message", msg))

    bridge.group_leave(9)
    seq = bridge.handler._seq
    bridge._on_group_leave_resp(MessageType.GROUP_LEAVE, seq, {"success": True})

    assert "9" not in bridge._groups
    assert ("group_left", {"group_id": "9", "groups": {}, "available_groups": {}}) in events


def test_web_bridge_group_create_join_keep_server_id_clear():
    bridge = WebBridge.__new__(WebBridge)
    bridge.handler = DummyHandler()
    bridge._groups = {}
    bridge._available_groups = {}
    events = []
    bridge._push = lambda event_type, data: events.append((event_type, data))
    bridge._push_msg = lambda msg: events.append(("new_message", msg))

    bridge._on_group_create_resp(MessageType.GROUP_CREATE, 1, {
        "success": True,
        "group_id": 2,
        "name": "1",
    })
    bridge._on_group_join_resp(MessageType.GROUP_JOIN, 2, {
        "success": True,
        "group_id": 1,
        "name": "group",
    })

    assert bridge._groups == {"2": "1", "1": "group"}
    system_messages = [event[1] for event in events if event[0] == "new_message"]
    assert any(msg["content"] == "Created group #2 \"1\"" for msg in system_messages)
    assert any(msg["content"] == "Joined group #1 \"group\"" for msg in system_messages)


def test_web_bridge_group_file_init_uses_group_context(monkeypatch):
    class InlineThread:
        def __init__(self, target, args=(), daemon=None):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(web_bridge_module.threading, "Thread", InlineThread)

    bridge = WebBridge.__new__(WebBridge)
    bridge._user_id = 1
    bridge._username = "alice"
    bridge._chat_type = "group"
    bridge._current_target = "9"
    bridge._current_target_id = 9
    bridge.conn = DummyConnection()
    bridge.handler = DummyHandler()
    bridge._pending_file_uploads = {}
    tmp_dir = make_runtime_dir("web_bridge_file_")
    sample = tmp_dir / "group.txt"
    sample.write_text("demo", encoding="utf-8")
    bridge._tk_file_dialog = lambda: str(sample)
    events = []
    bridge._push = lambda event_type, data: events.append((event_type, data))

    try:
        result = bridge.select_and_send_file()
        assert events == []
        bridge._on_file_init(MessageType.FILE_INIT, 1, {
            "success": True,
            "file_id": "file-g",
            "filename": "group.txt",
        })
    finally:
        remove_runtime_dir(tmp_dir)

    assert result["ok"] is True
    init_call = bridge.handler.calls[0]
    assert init_call[0] == "file_init"
    assert init_call[2] is None
    assert init_call[6] == 9
    assert events[-1][0] == "file_sent"
    assert events[-1][1]["chat_key"] == "group:9"
    assert events[-1][1]["related_type"] == "group"
    assert events[-1][1]["related_target"] == "9"


def test_web_bridge_demo_file_uses_explicit_group_context(monkeypatch):
    class InlineThread:
        def __init__(self, target, args=(), daemon=None):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(web_bridge_module.threading, "Thread", InlineThread)

    bridge = WebBridge.__new__(WebBridge)
    bridge._user_id = 1
    bridge._username = "alice"
    bridge._chat_type = "private"
    bridge._current_target = "bob"
    bridge._current_target_id = 2
    bridge.conn = DummyConnection()
    bridge.handler = DummyHandler()
    bridge._pending_file_uploads = {}
    tmp_dir = make_runtime_dir("web_bridge_demo_file_")
    sample = tmp_dir / "group-demo.txt"
    sample.write_text("demo", encoding="utf-8")
    events = []
    bridge._push = lambda event_type, data: events.append((event_type, data))

    try:
        result = bridge.demo_send_file(str(sample), chat_type="group", target_id="9")
        assert events == []
        bridge._on_file_init(MessageType.FILE_INIT, 1, {
            "success": True,
            "file_id": "file-g",
            "filename": "group-demo.txt",
        })
    finally:
        remove_runtime_dir(tmp_dir)

    assert result["ok"] is True
    init_call = bridge.handler.calls[0]
    assert init_call[0] == "file_init"
    assert init_call[2] is None
    assert init_call[6] == 9
    assert events[-1][0] == "file_sent"
    assert events[-1][1]["chat_key"] == "group:9"
    assert events[-1][1]["related_type"] == "group"
    assert events[-1][1]["related_target"] == "9"


def test_web_bridge_file_init_failure_does_not_send_chunks(monkeypatch):
    class RejectingHandler(DummyHandler):
        def send_file_init(self, from_id, to_id, filename, filesize, file_id, group_id=None):
            self.calls.append(("file_init", from_id, to_id, filename, filesize, file_id, group_id))
            return {"ok": True, "seq": 11, "client_file_id": file_id}

    bridge = WebBridge.__new__(WebBridge)
    bridge._user_id = 1
    bridge._username = "alice"
    bridge._chat_type = "private"
    bridge._current_target = "bob"
    bridge._current_target_id = 2
    bridge.conn = DummyConnection()
    bridge.handler = RejectingHandler()
    bridge._pending_file_uploads = {}
    tmp_dir = make_runtime_dir("web_bridge_file_reject_")
    sample = tmp_dir / "reject.txt"
    sample.write_text("demo", encoding="utf-8")
    bridge._tk_file_dialog = lambda: str(sample)
    bridge._chat_key = lambda chat_type, target: f"{chat_type}:{target}"
    events = []
    bridge._push_msg = lambda msg: events.append(msg)

    try:
        result = bridge.select_and_send_file()
        bridge._on_file_init(MessageType.FILE_INIT, 11, {
            "success": False,
            "error": "not_group_member",
        })
    finally:
        remove_runtime_dir(tmp_dir)

    assert result["ok"] is True
    assert [call[0] for call in bridge.handler.calls] == ["file_init"]
    assert bridge._pending_file_uploads == {}
    assert events[-1]["chat_key"] == "private:2"
    assert "not_group_member" in events[-1]["content"]


def test_web_bridge_file_ack_failure_reports_download_error():
    bridge = WebBridge.__new__(WebBridge)
    import threading
    bridge._dl_state = {
        "file-1": {
            "data": {},
            "remaining": 4,
            "event": threading.Event(),
            "chunk_size": 65536,
        }
    }

    bridge._on_file_ack(MessageType.FILE_ACK, 3, {
        "success": False,
        "file_id": "file-1",
        "offset": 0,
        "error": "not_receiver",
    })

    state = bridge._dl_state["file-1"]
    assert state["error"] == "not_receiver"
    assert state["event"].is_set()


def test_web_bridge_group_file_notification_routes_download_to_group():
    bridge = WebBridge.__new__(WebBridge)
    bridge._online_users = {"alice": 1, "bob": 2}
    bridge._download_dir = "."
    pushed = []
    bridge._push = lambda event_type, data: pushed.append((event_type, data))
    downloads = []
    bridge._gui_download_file = lambda file_id, filename, filesize, context: downloads.append(
        (file_id, filename, filesize, context)
    )

    bridge._on_file_init(MessageType.FILE_INIT, 1, {
        "status": "completed",
        "file_id": "file-g",
        "from_id": 2,
        "filename": "group.txt",
        "filesize": 4,
        "group_id": 9,
        "chat_key": "group:9",
    })

    assert pushed[0][0] == "file_incoming"
    assert pushed[0][1]["chat_key"] == "group:9"
    assert pushed[0][1]["related_type"] == "group"
    assert pushed[0][1]["related_target"] == "9"
    assert downloads == [(
        "file-g", "group.txt", 4,
        {
            "related_type": "group",
            "related_target": "9",
            "chat_key": "group:9",
            "group_id": "9",
        },
    )]
