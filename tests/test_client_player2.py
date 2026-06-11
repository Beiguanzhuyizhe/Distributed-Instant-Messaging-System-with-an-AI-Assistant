"""
Player2 客户端功能回归测试。

这些测试聚焦 CLI/GUI 共用的客户端行为：撤回、历史、文件传输和 ACK 对齐。
测试不启动真实服务器，而是用假连接/假 handler 捕获客户端发出的协议 payload。
"""

from pathlib import Path

from client.message_handler import MessageHandler
from client.protocol import MessageType
from client.cli import ChatCLI
from client.gui import ChatGUI


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

    def request_history(self, target_type, target_id, limit=50):
        self.calls.append(("history", target_type, target_id, limit))

    def send_recall(self, msg_id):
        self.calls.append(("recall", msg_id))

    def send_file_init(self, from_id, to_id, filename, filesize, file_id):
        self.calls.append(("file_init", from_id, to_id, filename, filesize, file_id))
        return {"ok": True, "seq": 1, "client_file_id": file_id}

    def send_file_data(self, file_id, chunk_data, chunk_index, total_chunks):
        self.calls.append(("file_data", file_id, chunk_index, total_chunks, chunk_data))
        return {"ok": True}


def test_message_handler_returns_tracking_info_for_private_message():
    conn = DummyConnection()
    handler = MessageHandler(conn)

    result = handler.send_private_msg(1, 2, "hello")

    assert result["ok"] is True
    assert result["seq"] == conn.sent[-1]["seq"]
    assert result["payload"] == conn.sent[-1]["payload"]
    assert result["client_msg_id"] == conn.sent[-1]["payload"]["msg_id"]
    assert conn.sent[-1]["msg_type"] == MessageType.PRIVATE_MSG


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
    sample = Path("now.md")

    cli = ChatCLI.__new__(ChatCLI)
    cli.handler = DummyHandler()
    cli._print = lambda *args, **kwargs: None
    cli._online_users = {"bob": 2}
    cli._user_id = 1

    cli._send_file("bob", str(sample))

    init_call = cli.handler.calls[0]
    data_call = cli.handler.calls[1]
    assert init_call[0] == "file_init"
    assert isinstance(init_call[-1], str)
    assert data_call[1] == init_call[-1]


def test_gui_private_history_resolves_selected_user_id():
    gui = ChatGUI()
    gui.handler = DummyHandler()
    gui._current_target = "alice"
    gui._current_target_id = 2
    gui._chat_type = "private"

    gui._menu_history()

    assert gui.handler.calls == [("history", "private", 2, 50)]
