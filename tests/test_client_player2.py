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


class DummyHandler:
    """记录 CLI/GUI 调用的高层发送接口。"""

    def __init__(self):
        self.calls = []
        self._seq = 0

    def request_history(self, target_type, target_id, limit=50):
        self.calls.append(("history", target_type, target_id, limit))

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
    assert ("group_left", {"group_id": "9", "groups": {}}) in events


def test_web_bridge_group_create_join_keep_server_id_clear():
    bridge = WebBridge.__new__(WebBridge)
    bridge._groups = {}
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
    assert ("new_message", {"type": "system", "content": "Created group #2 \"1\""}) in events
    assert ("new_message", {"type": "system", "content": "Joined group #1 \"group\""}) in events


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
    bridge.handler = DummyHandler()
    tmp_dir = make_runtime_dir("web_bridge_file_")
    sample = tmp_dir / "group.txt"
    sample.write_text("demo", encoding="utf-8")
    bridge._tk_file_dialog = lambda: str(sample)
    events = []
    bridge._push = lambda event_type, data: events.append((event_type, data))

    try:
        result = bridge.select_and_send_file()
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
