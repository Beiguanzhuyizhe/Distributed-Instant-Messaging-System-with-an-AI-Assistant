"""
客户端 CLI 界面 — 使用 prompt_toolkit 滚动式聊天界面（不依赖 Live）
消息由后台线程直接打印，主线程只处理输入，互不干扰
"""

import time
import threading
import os
import uuid
from html import escape as html_escape
from datetime import datetime
from typing import Optional
from collections import deque

from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.formatted_text import HTML

from protocol import MessageType
from connection import ChatConnection
from message_handler import MessageHandler
from message_store import MessageStore
from p2p import P2PClient


CMD_COMPLETER = WordCompleter([
    "/msg", "/join", "/create", "/leave", "/group",
    "/sendfile", "/recall", "/users", "/history", "/quit",
    "/help", "/clear", "/contacts",
], ignore_case=True)


def _now() -> int:
    return int(time.time())


def _fmt_time(ts) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%H:%M")
    except (ValueError, OSError):
        return ""


def _html(value) -> str:
    """Escape dynamic text before inserting it into prompt_toolkit HTML."""
    return html_escape("" if value is None else str(value), quote=False)


class ChatCLI:
    """prompt_toolkit 滚动式聊天 CLI — 后台线程打印消息，主线程读输入"""

    def __init__(self, host=None, port=None):
        from config import Config
        self.host = host or Config.SERVER_HOST
        self.port = port or Config.SERVER_PORT

        self.conn = ChatConnection()
        self.handler = MessageHandler(self.conn)
        self.p2p = P2PClient()
        self.store = MessageStore()

        self._running = True
        self._user_id: Optional[int] = None
        self._username: Optional[str] = None
        self._messages: list[dict] = []
        self._online_users: dict = {}       # {username: user_id}
        self._groups: dict = {}             # {group_id: name}
        self._available_groups: dict = {}   # {group_id: metadata}
        self._current_target: Optional[str] = None
        self._current_target_id: Optional[int] = None
        self._chat_type: str = "private"
        self._logged_in = False
        self._password_hash: Optional[str] = None

        # 登录同步
        self._login_event = threading.Event()
        self._login_ok = False
        self._register_ok = False
        self._auth_message = ""
        self._pending_acks: dict[int, dict] = {}
        self._last_sent_msg_id: Optional[str] = None

        self._msg_lock = threading.Lock()

        self._pt_session = self._new_prompt_session()

        # 文件下载
        self._download_dir = os.path.join(os.path.dirname(__file__), "downloads")
        os.makedirs(self._download_dir, exist_ok=True)
        self._dl_state = {}
        self._pending_files = {}

        # 消息显示队列（后台线程入队，保证 print 不乱）
        self._display_queue = deque()
        self._display_lock = threading.Lock()
        self._prompt_busy = False

        self._register_callbacks()

    @staticmethod
    def _new_prompt_session():
        return PromptSession(
            history=InMemoryHistory(),
            completer=CMD_COMPLETER,
        )

    # =============================================================
    # 回调注册
    # =============================================================

    def _register_callbacks(self):
        reg = self.handler.register
        reg(MessageType.LOGIN_RESP, self._on_login_resp)
        reg(MessageType.REGISTER_RESP, self._on_register_resp)
        reg(MessageType.PRIVATE_MSG, self._on_private_msg)
        reg(MessageType.GROUP_MSG, self._on_group_msg)
        reg(MessageType.STATUS_UPDATE, self._on_status_update)
        reg(MessageType.AI_RESP, self._on_ai_resp)
        reg(MessageType.CONTENT_WARN, self._on_content_warn)
        reg(MessageType.ERROR, self._on_error)
        reg(MessageType.MSG_RECALL, self._on_recall)
        reg(MessageType.HISTORY_RESP, self._on_history)
        reg(MessageType.ONLINE_USERS, self._on_online_users)
        reg(MessageType.GROUP_CREATE, self._on_group_create_resp)
        reg(MessageType.GROUP_JOIN, self._on_group_join_resp)
        reg(MessageType.GROUP_LEAVE, self._on_group_leave_resp)
        reg(MessageType.FILE_INIT, self._on_file_init)
        reg(MessageType.FILE_ACK, self._on_file_ack)

        self.conn.on_disconnected(self._on_disconnected)
        self.conn.on_connected(self._on_reconnected)

        # P2P 打洞响应路由
        self.p2p.register_message_handler(self.handler)

    # ---------------------------------------------------------------
    # 消息打印（后台线程调用，print_formatted_text 线程安全）
    # ---------------------------------------------------------------

    def _print(self, msg_type: str, ts: str, sender: str, content: str, extra: str = ""):
        """在后台线程直接打印格式化消息"""
        if msg_type == "system":
            print_formatted_text(HTML(
                f'<ansicyan>{_html(ts)}</ansicyan> '
                f'<ansiwhite>{_html(content)}</ansiwhite>'
            ))
        else:
            is_self = (sender == self._username)
            color = "ansigreen" if is_self else "ansiyellow"
            tag = "You" if is_self else sender
            label = f"<{color}>{_html(tag)}:</{color}>"
            if extra:
                label += f" <ansicyan>{_html(extra)}</ansicyan>"
            print_formatted_text(HTML(
                f'<ansiwhite>{_html(ts)}</ansiwhite> '
                f'{label} '
                f'{_html(content)}'
            ))

    def _remember_pending(self, send_result: dict, msg: dict):
        """记录待 ACK 的本地消息，等服务端返回 UUID msg_id 后再回写。"""
        seq = send_result.get("seq") if send_result else None
        if seq is not None:
            self._pending_acks[seq] = msg

    def _append_and_store(self, msg: dict):
        """同时写入内存消息列表和本地 JSON 历史。"""
        with self._msg_lock:
            self._messages.append(msg)
        if self._username:
            self.store.add_message(self._username, msg)

    def _apply_message_ack(self, seq: int, payload: dict):
        """用服务端 ACK 更新本地消息 ID，保证 /recall 使用真实 UUID。"""
        msg = self._pending_acks.pop(seq, None)
        if not msg:
            return
        local_msg_id = str(msg.get("local_msg_id", msg.get("msg_id", "")))
        msg["status"] = payload.get("status", "ack")
        if payload.get("timestamp"):
            msg["timestamp"] = payload["timestamp"]
        server_msg_id = str(payload.get("msg_id", "") or "")
        if not server_msg_id:
            if self._username:
                self.store.update_message_status(
                    self._username, local_msg_id, msg["status"],
                )
            if payload.get("error"):
                self._print(
                    "system", _fmt_time(_now()), "",
                    f"Message rejected: {payload['error']}",
                )
            return
        msg["server_msg_id"] = server_msg_id
        msg["msg_id"] = server_msg_id
        self._last_sent_msg_id = server_msg_id
        if self._username:
            self.store.update_message_id(
                self._username, local_msg_id, server_msg_id,
                timestamp=msg.get("timestamp"), status=msg.get("status", ""),
            )
        if server_msg_id:
            self._print(
                "system", _fmt_time(_now()), "",
                f"Message confirmed: {server_msg_id}",
            )

    def _mark_recalled(self, msg_id: str):
        """将内存和本地历史里的消息标记为已撤回。"""
        target = str(msg_id)
        if not target:
            return
        with self._msg_lock:
            for msg in self._messages:
                ids = {
                    str(msg.get("msg_id", "")),
                    str(msg.get("local_msg_id", "")),
                    str(msg.get("server_msg_id", "")),
                }
                if target in ids:
                    msg["is_recalled"] = True
                    msg["content"] = "[已撤回]"
        if self._username:
            self.store.mark_recalled(self._username, target)

    def _resolve_history_target(self, target: str):
        """把 CLI 输入的用户名/群号转换为服务端需要的数字 target_id。"""
        if self._chat_type == "group":
            try:
                return "group", int(target)
            except (TypeError, ValueError):
                self._print("system", _fmt_time(_now()), "", "Usage: /history <group_id>")
                return None, None
        if target in self._online_users:
            return "private", self._online_users[target]
        try:
            return "private", int(target)
        except (TypeError, ValueError):
            self._print("system", _fmt_time(_now()), "", f"User '{target}' not online")
            return None, None

    def _history_sender(self, msg: dict) -> str:
        """历史消息只有 sender_id 时，用当前用户和在线表尽量还原显示名。"""
        sender_id = msg.get("sender_id", msg.get("from_id"))
        if sender_id == self._user_id:
            return self._username or "You"
        for name, uid in self._online_users.items():
            if uid == sender_id:
                return name
        return f"User#{sender_id}" if sender_id else msg.get("sender", "unknown")

    @staticmethod
    def _chat_key(chat_type: str, target_id) -> str:
        return f"{chat_type}:{target_id}"

    @classmethod
    def _with_chat_context(cls, msg: dict, chat_type: str, target_id) -> dict:
        """为本地历史添加稳定会话键，避免用用户名/发送者做模糊过滤。"""
        target = str(target_id)
        msg["related_type"] = chat_type
        msg["related_target"] = target
        msg["chat_key"] = cls._chat_key(chat_type, target)
        return msg

    def _payload_chat_context(self, payload: dict) -> dict:
        """Prefer explicit server routing metadata over the currently selected CLI chat."""
        related_type = payload.get("related_type")
        related_target = payload.get("related_target")
        if not related_type and payload.get("group_id"):
            related_type = "group"
            related_target = str(payload.get("group_id"))
        if not related_type and payload.get("chat_key"):
            key = str(payload.get("chat_key"))
            if ":" in key:
                related_type, related_target = key.split(":", 1)
        if related_type and related_target:
            return {
                "related_type": str(related_type),
                "related_target": str(related_target),
                "chat_key": self._chat_key(str(related_type), related_target),
            }
        return self._current_chat_context()

    def _current_chat_context(self) -> dict:
        if self._chat_type == "private":
            target = self._current_target_id
        else:
            target = self._current_target
        if target is None or target == "":
            return {}
        return {
            "related_type": self._chat_type,
            "related_target": str(target),
            "chat_key": self._chat_key(self._chat_type, target),
        }

    # =============================================================
    # 消息回调
    # =============================================================

    def _on_login_resp(self, msg_type, seq, payload):
        self._login_ok = bool(payload.get("success"))
        self._auth_message = payload.get("message") or payload.get("error") or ""
        if self._login_ok:
            self._user_id = payload.get("user_id")
            self._username = payload.get("username", self._username)
            groups = payload.get("groups")
            if isinstance(groups, dict):
                self._groups = {str(k): v for k, v in groups.items()}
            available_groups = payload.get("available_groups")
            if isinstance(available_groups, dict):
                self._available_groups = {str(k): v for k, v in available_groups.items()}
        self._login_event.set()

    def _on_register_resp(self, msg_type, seq, payload):
        self._register_ok = bool(payload.get("success"))
        self._login_ok = False
        self._auth_message = payload.get("message") or payload.get("error") or ""
        self._login_event.set()

    def _on_private_msg(self, msg_type, seq, payload):
        # 跳过服务端 ACK（_ack=True）和自身发送的回显
        if payload.get("_ack"):
            self._apply_message_ack(seq, payload)
            return
        if payload.get("from_id") == self._user_id:
            return
        sender = payload.get("from_username", payload.get("sender", f"User#{payload.get('from_id', '')}"))
        content = payload.get("content", "")
        ts = _fmt_time(payload.get("timestamp", _now()))
        peer_id = payload.get("from_id", 0)
        msg = {"type": "private", "sender": sender, "content": content,
               "msg_id": str(payload.get("msg_id", "")), "timestamp": payload.get("timestamp", _now()),
               "from_id": peer_id, "receiver_id": payload.get("to_id", self._user_id),
               "target_id": peer_id}
        self._with_chat_context(msg, "private", peer_id)
        self._append_and_store(msg)
        self._print("private", ts, sender, content)

    def _on_group_msg(self, msg_type, seq, payload):
        # 跳过服务端 ACK 和自身发送的回显
        if payload.get("_ack"):
            self._apply_message_ack(seq, payload)
            return
        if payload.get("from_id") == self._user_id:
            return
        sender = payload.get("from_username", payload.get("sender", f"User#{payload.get('from_id', '')}"))
        gid = str(payload.get("group_id", ""))
        content = payload.get("content", "")
        ts = _fmt_time(payload.get("timestamp", _now()))
        gname = self._groups.get(gid, f"Group#{gid}")
        msg = {"type": "group", "group_id": gid, "group_name": gname,
               "sender": sender, "content": content,
               "msg_id": str(payload.get("msg_id", "")), "timestamp": _now(),
               "from_id": payload.get("from_id", 0), "target_id": gid}
        self._with_chat_context(msg, "group", gid)
        self._append_and_store(msg)
        self._print("group", ts, sender, content, extra=f"@{gname}")

    def _on_status_update(self, msg_type, seq, payload):
        username = payload.get("username", "")
        uid = payload.get("user_id", 0)
        is_online = payload.get("is_online", False)
        if is_online:
            self._online_users[username] = uid
        else:
            self._online_users.pop(username, None)

    def _on_ai_resp(self, msg_type, seq, payload):
        content = payload.get("content", payload.get("reply", ""))
        if content:
            ts = _fmt_time(_now())
            self._print("system", ts, "", f"[AI] {content}")
            msg = {"type": "system", "content": f"[AI] {content}", "timestamp": _now()}
            msg.update(self._payload_chat_context(payload))
            with self._msg_lock:
                self._messages.append(msg)

    def _on_content_warn(self, msg_type, seq, payload):
        msg = payload.get("message", "Content warning")
        ts = _fmt_time(_now())
        self._print("system", ts, "", f"[WARN] {msg}")
        item = {"type": "system", "content": f"[WARN] {msg}", "timestamp": _now()}
        item.update(self._payload_chat_context(payload))
        with self._msg_lock:
            self._messages.append(item)

    def _on_error(self, msg_type, seq, payload):
        code = payload.get("code", -1)
        msg = payload.get("message", "Unknown error")
        ts = _fmt_time(_now())
        self._print("system", ts, "", f"[Error {code}] {msg}")
        item = {"type": "system", "content": f"[Error {code}] {msg}", "timestamp": _now()}
        item.update(self._payload_chat_context(payload))
        with self._msg_lock:
            self._messages.append(item)

    def _on_recall(self, msg_type, seq, payload):
        if payload.get("success") is False:
            err = payload.get("error") or payload.get("message", "recall failed")
            self._print("system", _fmt_time(_now()), "", f"Recall failed: {err}")
            return
        msg_id = str(payload.get("msg_id", ""))
        self._mark_recalled(msg_id)
        mid = msg_id[:8]
        ts = _fmt_time(_now())
        self._print("system", ts, "", f"Message {mid}... was recalled")
        msg = {"type": "system", "content": f"Message {mid}... recalled", "timestamp": _now()}
        msg.update(self._current_chat_context())
        self._append_and_store(msg)

    def _on_history(self, msg_type, seq, payload):
        history = payload.get("messages", [])
        formatted_history = []
        for m in history:
            msg_kind = "group" if payload.get("type") == "group" or m.get("group_id") else "private"
            if msg_kind == "group":
                target_id = str(m.get("group_id") or payload.get("target_id") or "")
            else:
                sender_id = m.get("sender_id", m.get("from_id", 0))
                receiver_id = m.get("receiver_id", m.get("to_id", 0))
                target_id = payload.get("target_id") or (
                    receiver_id if str(sender_id) == str(self._user_id) else sender_id
                )
            item = dict(m)
            item["type"] = msg_kind
            item["target_id"] = str(target_id)
            self._with_chat_context(item, msg_kind, target_id)
            formatted_history.append(item)
        with self._msg_lock:
            for m in formatted_history:
                self._messages.append(m)
        ts = _fmt_time(_now())
        self._print("system", ts, "", f"Loaded {len(history)} history messages")
        for m in history:
            sender = self._history_sender(m)
            content = "[已撤回]" if m.get("recalled") or m.get("is_recalled") else m.get("content", "")
            msg_kind = "group" if payload.get("type") == "group" or m.get("group_id") else "private"
            extra_parts = []
            if msg_kind == "group" and m.get("group_id"):
                extra_parts.append(f"@Group#{m.get('group_id')}")
            if m.get("msg_id"):
                extra_parts.append(f"id:{m.get('msg_id')}")
            extra = " ".join(extra_parts)
            self._print(
                msg_kind,
                _fmt_time(m.get("created_at", m.get("timestamp", _now()))),
                sender,
                content,
                extra=extra,
            )

    def _on_online_users(self, msg_type, seq, payload):
        users = payload.get("users", [])
        self._online_users = {}
        names = []
        for u in users:
            uid = u.get("id", 0)
            name = u.get("username", f"User#{uid}")
            self._online_users[name] = uid
            names.append(name)
        if self._username and self._user_id:
            self._online_users.setdefault(self._username, self._user_id)
            if self._username not in names:
                names.append(self._username)
        groups = payload.get("groups")
        if isinstance(groups, dict):
            self._groups = {str(k): v for k, v in groups.items()}
        available_groups = payload.get("available_groups")
        if isinstance(available_groups, dict):
            self._available_groups = {str(k): v for k, v in available_groups.items()}
        ts = _fmt_time(_now())
        self._print("system", ts, "", f"Online ({len(self._online_users)}): {', '.join(names)}")

    def _on_group_create_resp(self, msg_type, seq, payload):
        ts = _fmt_time(_now())
        if payload.get("success"):
            gid = str(payload.get("group_id", ""))
            name = payload.get("name", "")
            self._groups[gid] = name
            self._print("system", ts, "", f"Group '{name}' created (ID: {gid})")
        else:
            err = payload.get("error") or payload.get("message", "")
            self._print("system", ts, "", f"Create group failed: {err}")
        with self._msg_lock:
            self._messages.append({"type": "system", "content": "...", "timestamp": _now()})

    def _on_group_join_resp(self, msg_type, seq, payload):
        ts = _fmt_time(_now())
        gid = str(payload.get("group_id", ""))
        if payload.get("success"):
            name = payload.get("name", gid)
            self._groups[gid] = name
            self._print("system", ts, "", f"Joined group '{name}'")
        else:
            err = payload.get("error") or payload.get("message", "")
            self._print("system", ts, "", f"Join group failed: {err}")
        with self._msg_lock:
            self._messages.append({"type": "system", "content": "...", "timestamp": _now()})

    def _on_group_leave_resp(self, msg_type, seq, payload):
        ts = _fmt_time(_now())
        gid = str(payload.get("group_id", ""))
        if payload.get("success"):
            self._groups.pop(gid, None)
            self._print("system", ts, "", f"Left group {gid}")
        else:
            err = payload.get("error") or payload.get("message", "")
            self._print("system", ts, "", f"Leave group failed: {err}")
        if self._current_target == gid and self._chat_type == "group":
            self._current_target = None
            self._chat_type = "private"
        with self._msg_lock:
            self._messages.append({"type": "system", "content": "...", "timestamp": _now()})

    def _on_file_init(self, msg_type, seq, payload):
        from_id = payload.get("from_id", 0)
        filename = payload.get("filename", "unknown")
        filesize = payload.get("filesize", 0)
        status = payload.get("status", "")
        if status != "completed":
            return
        sender = f"User#{from_id}"
        for name, uid in self._online_users.items():
            if uid == from_id:
                sender = name
                break
        ts = _fmt_time(_now())
        self._print("system", ts, "", f"Incoming file from {sender}: {filename} ({filesize} bytes)")
        threading.Thread(target=self._download_file,
                         args=(payload.get("file_id", ""), filename, filesize), daemon=True).start()

    def _on_file_ack(self, msg_type, seq, payload):
        file_id = payload.get("file_id", "")
        offset = payload.get("offset", 0)
        data_b64 = payload.get("data", "")
        if payload.get("success") is False or not data_b64:
            return
        state = self._dl_state.get(file_id)
        if not state:
            return
        import base64
        data = base64.b64decode(data_b64)
        state["data"][offset] = data
        state["remaining"] -= len(data)
        if state["remaining"] <= 0:
            state["event"].set()

    def _download_file(self, file_id, filename, filesize):
        CHUNK_SIZE = 64 * 1024
        dest = os.path.join(self._download_dir, filename)
        if filesize == 0:
            with open(dest, "wb"):
                pass
            self._print("system", _fmt_time(_now()), "", f"Downloaded: {filename} (0 bytes)")
            return
        state = {"data": {}, "remaining": filesize, "event": threading.Event(), "chunk_size": CHUNK_SIZE}
        self._dl_state[file_id] = state
        for offset in range(0, filesize, CHUNK_SIZE):
            self.handler.request_file_chunk(file_id, offset)
        if not state["event"].wait(timeout=30):
            self._print("system", _fmt_time(_now()), "", f"File download timed out: {filename}")
            self._dl_state.pop(file_id, None)
            return
        with open(dest, "wb") as f:
            for offset in sorted(state["data"].keys()):
                f.write(state["data"][offset])
        self._dl_state.pop(file_id, None)
        self._print("system", _fmt_time(_now()), "", f"Downloaded: {filename} ({filesize} bytes)")

    def _on_disconnected(self):
        self._print("system", _fmt_time(_now()), "", "Disconnected. Reconnecting...")

    def _on_reconnected(self):
        self._print("system", _fmt_time(_now()), "", "Reconnected.")
        if self._logged_in and self._username and self._password_hash:
            self._login_event.clear()
            self.handler.send_login(self._username, self._password_hash)
            if self._login_event.wait(timeout=5) and self._login_ok:
                self.handler.request_online_users()

    # =============================================================
    # 登录界面
    # =============================================================

    def _do_login(self) -> bool:
        print_formatted_text(HTML(
            '<ansicyan>=== Chat System v1.0 ===</ansicyan>\n'
            '<ansiwhite>Distributed Instant Messaging</ansiwhite>'
        ))

        while self._running and not self._logged_in:
            action = self._pt_session.prompt(
                "Login (login/register/quit): ",
                default="login",
            ).strip().lower()

            if action == "quit":
                return False
            if action not in ("login", "register"):
                continue

            username = self._pt_session.prompt("Username: ").strip()
            password = self._pt_session.prompt("Password: ", is_password=True).strip()

            if not username or not password:
                print_formatted_text(HTML('<ansired>Username and password required</ansired>'))
                continue

            if not self.conn.is_connected:
                print_formatted_text(HTML('<ansired>Not connected to server</ansired>'))
                continue

            self._login_event.clear()
            self._login_ok = False
            self._register_ok = False
            self._auth_message = ""
            self._username = username
            self._password_hash = password

            if action == "login":
                self.handler.send_login(username, password)
            else:
                self.handler.send_register(username, password)

            if self._login_event.wait(timeout=10):
                if action == "register":
                    if self._register_ok:
                        print_formatted_text(HTML(
                            f'<ansigreen>Registration successful for {_html(username)}. Please login.</ansigreen>'
                        ))
                    else:
                        msg = self._auth_message or "Registration failed."
                        print_formatted_text(HTML(f'<ansired>{_html(msg)}</ansired>'))
                    continue

                if self._login_ok:
                    self._logged_in = True
                    self._online_users[self._username] = self._user_id
                    self.handler.request_online_users()
                    print_formatted_text(HTML(f'<ansigreen>Welcome, {_html(username)}!</ansigreen>'))
                    time.sleep(0.3)
                    return True
                else:
                    msg = self._auth_message or "Login failed. Check your credentials."
                    print_formatted_text(HTML(f'<ansired>{_html(msg)}</ansired>'))
            else:
                print_formatted_text(HTML('<ansired>Login timeout.</ansired>'))

        return False

    # =============================================================
    # 主聊天循环
    # =============================================================

    def _chat_loop(self):
        """主循环：prompt_toolkit 读取输入，消息由后台线程直接打印"""
        self._pt_session = self._new_prompt_session()
        self._print("system", _fmt_time(_now()), "",
                     "Connected. Type /help for commands. | "
                     f"Target: {self._current_target or 'none'} | "
                     f"Online: {len(self._online_users)}")

        while self._running:
            try:
                text = self._pt_session.prompt(
                    "> ",
                    is_password=False,
                    bottom_toolbar=f" Target: {self._current_target or 'none'} | Online: {len(self._online_users)} | Groups: {len(self._groups)} ",
                ).strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not text:
                continue
            if text == "/quit":
                break

            if text.startswith("/"):
                self._handle_command(text)
            else:
                self._handle_send(text)

    # =============================================================
    # 命令处理
    # =============================================================

    def _handle_command(self, text: str):
        parts = text.strip().split(maxsplit=2)
        cmd = parts[0].lower()

        if cmd == "/help":
            self._show_help()
        elif cmd == "/users":
            self.handler.request_online_users()
        elif cmd == "/clear":
            pass  # 滚动式界面不需要清屏
        elif cmd == "/msg":
            if len(parts) >= 3:
                target, content = parts[1], parts[2]
                self._current_target = target
                self._current_target_id = self._online_users.get(target)
                self._chat_type = "private"
                self._send_private(target, content)
            else:
                self._print("system", _fmt_time(_now()), "", "Usage: /msg <username> <content>")
        elif cmd == "/join":
            if len(parts) >= 2:
                try:
                    self.handler.group_join(int(parts[1]), self._user_id or 0)
                except ValueError:
                    self._print("system", _fmt_time(_now()), "", "Usage: /join <group_id>")
            else:
                self._print("system", _fmt_time(_now()), "", "Usage: /join <group_id>")
        elif cmd == "/create":
            if len(parts) >= 2:
                self.handler.group_create(parts[1], self._user_id or 0)
            else:
                self._print("system", _fmt_time(_now()), "", "Usage: /create <group_name>")
        elif cmd == "/leave":
            gid = parts[1] if len(parts) >= 2 else (self._current_target if self._chat_type == "group" else "")
            if gid:
                try:
                    self.handler.group_leave(int(gid), self._user_id or 0)
                except ValueError:
                    self._print("system", _fmt_time(_now()), "", "Invalid group_id")
            else:
                self._print("system", _fmt_time(_now()), "", "Usage: /leave <group_id>")
        elif cmd == "/group":
            if len(parts) >= 3:
                try:
                    gid = int(parts[1])
                except ValueError:
                    self._print("system", _fmt_time(_now()), "", "Usage: /group <group_id> <content>")
                    return
                self._current_target = str(gid)
                self._chat_type = "group"
                self._send_group(gid, parts[2])
            else:
                self._print("system", _fmt_time(_now()), "", "Usage: /group <group_id> <content>")
        elif cmd == "/sendfile":
            if len(parts) >= 3:
                self._send_file(parts[1], parts[2])
            else:
                self._print("system", _fmt_time(_now()), "", "Usage: /sendfile <username> <filepath>")
        elif cmd == "/recall":
            if len(parts) >= 2:
                self.handler.send_recall(parts[1])
            else:
                self._print("system", _fmt_time(_now()), "", "Usage: /recall <msg_id>")
        elif cmd == "/history":
            target = parts[1] if len(parts) >= 2 else (self._current_target or "")
            if target:
                ttype, target_id = self._resolve_history_target(target)
                if target_id is not None:
                    self.handler.request_history(ttype, target_id)
            else:
                self._print("system", _fmt_time(_now()), "", "Usage: /history <target>")
        elif cmd == "/contacts":
            self._show_contacts()
        else:
            self._print("system", _fmt_time(_now()), "", f"Unknown: {cmd}. Type /help")

    def _show_help(self):
        self._print("system", _fmt_time(_now()), "", "\n".join([
            "Commands:",
            "  /msg <user> <text>      Send private message",
            "  /join <group_id>        Join a group",
            "  /create <name>          Create a group",
            "  /leave <group_id>       Leave a group",
            "  /group <id> <text>      Send to group",
            "  @AI <question>          Ask AI",
            "  /sendfile <user> <path> Send file",
            "  /recall <msg_id>        Recall message",
            "  /users                  Online users",
            "  /history <target>       Load history",
            "  /contacts               Show contacts",
            "  /help                   This help",
            "  /quit                   Exit",
        ]))

    def _show_contacts(self):
        online = ", ".join(sorted(self._online_users.keys())) if self._online_users else "(none)"
        groups = ", ".join(f"#{n}({gid})" for gid, n in self._groups.items()) if self._groups else "(none)"
        self._print("system", _fmt_time(_now()), "",
                     f"Online ({len(self._online_users)}): {online}  |  Groups ({len(self._groups)}): {groups}")

    # =============================================================
    # 消息发送
    # =============================================================

    def _handle_send(self, text: str):
        stripped = text.strip()
        if stripped.upper().startswith("@AI"):
            query = stripped[3:].strip()
            if not query:
                self._print("system", _fmt_time(_now()), "", "Usage: @AI <question>")
                return
            if self._chat_type != "group" or not self._current_target:
                self._print("system", _fmt_time(_now()), "", "@AI is only available in a group chat. Use /group <id> <text> first.")
                return
            if self._user_id:
                self.handler.send_ai_query(self._user_id, int(self._current_target), query)
            return

        if self._chat_type == "group" and self._current_target:
            self._send_group(int(self._current_target), text)
        elif self._current_target:
            self._send_private(self._current_target, text)
        elif self._user_id:
            self._print("system", _fmt_time(_now()), "", "No target. Use /msg <user> <text> or /group <id> <text>")

    def _send_private(self, target: str, content: str):
        if not self._user_id:
            return
        target_id = self._online_users.get(target)
        if not target_id:
            self._print("system", _fmt_time(_now()), "", f"User '{target}' not online")
            return
        result = self.handler.send_private_msg(self._user_id, target_id, content)
        ts = _fmt_time(_now())
        self._print("private", ts, self._username or "You", content)
        local_msg_id = str(result.get("client_msg_id", "")) if result else ""
        msg = {"type": "private", "sender": self._username or "You",
               "receiver": target, "receiver_id": target_id, "target_id": target_id,
               "content": content, "local_msg_id": local_msg_id,
               "msg_id": local_msg_id, "timestamp": _now(),
               "status": "pending", "from_id": self._user_id}
        self._with_chat_context(msg, "private", target_id)
        self._append_and_store(msg)
        self._remember_pending(result, msg)

    def _send_group(self, gid: int, content: str):
        if not self._user_id:
            return
        if self._groups and str(gid) not in self._groups:
            self._print("system", _fmt_time(_now()), "", f"You are not in group {gid}. Use /join <group_id> first.")
            return
        result = self.handler.send_group_msg(self._user_id, gid, content)
        ts = _fmt_time(_now())
        gname = self._groups.get(str(gid), str(gid))
        self._print("group", ts, self._username or "You", content, extra=f"@{gname}")
        local_msg_id = str(result.get("client_msg_id", "")) if result else ""
        msg = {"type": "group", "group_id": str(gid), "target_id": str(gid),
               "group_name": gname, "sender": self._username or "You",
               "content": content, "local_msg_id": local_msg_id,
               "msg_id": local_msg_id, "timestamp": _now(),
               "status": "pending", "from_id": self._user_id}
        self._with_chat_context(msg, "group", gid)
        self._append_and_store(msg)
        self._remember_pending(result, msg)

    def _send_file(self, target: str, filepath: str):
        if not os.path.exists(filepath):
            self._print("system", _fmt_time(_now()), "", f"File not found: {filepath}")
            return
        filesize = os.path.getsize(filepath)
        file_id = str(uuid.uuid4())
        filename = os.path.basename(filepath)
        target_id = self._online_users.get(target)
        if not target_id:
            self._print("system", _fmt_time(_now()), "", f"User '{target}' not online")
            return

        self.handler.send_file_init(self._user_id or 0, target_id, filename, filesize, file_id)
        self._print("system", _fmt_time(_now()), "", f"Sending: {filename} ({filesize} bytes)")

        chunk_size = 64 * 1024
        with open(filepath, "rb") as f:
            idx = 0
            total = (filesize + chunk_size - 1) // chunk_size
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                self.handler.send_file_data(file_id, chunk, idx, total)
                idx += 1
                time.sleep(0.01)
        self._print("system", _fmt_time(_now()), "", f"Sent: {filename}")

    # =============================================================
    # 主入口
    # =============================================================

    def run(self):
        if not self.conn.connect(self.host, self.port):
            print_formatted_text(HTML(f'<ansired>Cannot connect to {self.host}:{self.port}</ansired>'))
            return None

        if not self._do_login():
            self.conn.close()
            return None

        try:
            self._chat_loop()
        except Exception as e:
            print_formatted_text(HTML(f'<ansired>Error: {e}</ansired>'))
        finally:
            self._running = False
            self.conn.close()

        return self._username
