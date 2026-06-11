"""
客户端 GUI 界面 — 基于 tkinter 的跨平台图形化聊天客户端
"""

import time
import os
import threading
import uuid
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox, simpledialog
from typing import Optional

from protocol import MessageType
from connection import ChatConnection
from message_handler import MessageHandler
from message_store import MessageStore
from p2p import P2PClient


def _now() -> int:
    return int(time.time())


class ChatGUI:
    """基于 tkinter 的图形化聊天客户端"""

    def __init__(self, host=None, port=None):
        from config import Config
        self.host = host or Config.SERVER_HOST
        self.port = port or Config.SERVER_PORT

        self.conn = ChatConnection()
        self.handler = MessageHandler(self.conn)
        self.p2p = P2PClient()
        self.store = MessageStore()

        self._user_id: Optional[int] = None
        self._username: Optional[str] = None
        self._messages: list[dict] = []
        self._online_users: dict = {}
        self._groups: dict = {}
        self._current_target: Optional[str] = None
        self._current_target_id: Optional[int] = None
        self._chat_type: str = "private"
        self._logged_in = False
        self._password_hash: Optional[str] = None
        self._pending_acks: dict[int, dict] = {}
        self._last_sent_msg_id: Optional[str] = None
        self._download_dir = os.path.join(os.path.dirname(__file__), "downloads")
        os.makedirs(self._download_dir, exist_ok=True)
        self._dl_state = {}

        self._login_win: Optional[tk.Tk] = None
        self._main_win: Optional[tk.Tk] = None
        self._msg_area: Optional[scrolledtext.ScrolledText] = None
        self._msg_input: Optional[ttk.Entry] = None
        self._contact_tree: Optional[ttk.Treeview] = None
        self._status_bar: Optional[ttk.Label] = None
        self._chat_title: Optional[ttk.Label] = None

        self._register_callbacks()

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

    def _safe_call(self, fn):
        """在主线程安全调度 tkinter 操作。fn 为无参 callable（用 lambda 包装）"""
        win = self._main_win or self._login_win
        if win and win.winfo_exists():
            win.after(0, fn)

    def _remember_pending(self, send_result: dict, msg: dict):
        """记录待 ACK 的本地消息，用服务端 ACK 回写真实 msg_id。"""
        seq = send_result.get("seq") if send_result else None
        if seq is not None:
            self._pending_acks[seq] = msg

    def _append_and_store(self, msg: dict):
        """同时写入 GUI 内存消息列表和本地 JSON 历史。"""
        self._messages.append(msg)
        if self._username:
            self.store.add_message(self._username, msg)

    def _apply_message_ack(self, seq: int, payload: dict):
        """把服务端 ACK 返回的 UUID msg_id 写回本地消息。"""
        msg = self._pending_acks.pop(seq, None)
        if not msg:
            return
        local_msg_id = str(msg.get("local_msg_id", msg.get("msg_id", "")))
        msg["status"] = payload.get("status", "ack")
        if payload.get("timestamp"):
            msg["timestamp"] = payload["timestamp"]
        server_msg_id = str(payload.get("msg_id", "") or "")
        if not server_msg_id:
            return
        msg["server_msg_id"] = server_msg_id
        msg["msg_id"] = server_msg_id
        self._last_sent_msg_id = server_msg_id
        if self._username:
            self.store.update_message_id(
                self._username, local_msg_id, server_msg_id,
                timestamp=msg.get("timestamp"), status=msg.get("status", ""),
            )

    def _mark_recalled(self, msg_id: str):
        """将内存和本地 JSON 历史里的消息标记为已撤回。"""
        target = str(msg_id)
        if not target:
            return
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

    def _history_sender(self, msg: dict) -> str:
        """历史消息只有 sender_id 时，用当前用户和在线表尽量还原显示名。"""
        sender_id = msg.get("sender_id", msg.get("from_id"))
        if sender_id == self._user_id:
            return self._username or "You"
        for name, uid in self._online_users.items():
            if uid == sender_id:
                return name
        return f"User#{sender_id}" if sender_id else msg.get("sender", "unknown")

    # =============================================================
    # 消息回调（在后台线程执行）
    # =============================================================

    def _on_login_resp(self, msg_type, seq, payload):
        if payload.get("success"):
            self._user_id = payload.get("user_id")
            self._username = payload.get("username", self._username)
            self._logged_in = True
            self._safe_call(self._transition_to_main)
        else:
            msg = payload.get("error") or payload.get("message", "Login failed")
            if self._login_win:
                self._safe_call(lambda: messagebox.showerror("Error", msg, parent=self._login_win))

    def _on_register_resp(self, msg_type, seq, payload):
        if payload.get("success"):
            self._safe_call(lambda: messagebox.showinfo(
                "Success", "Registration successful! Please login.",
                parent=self._login_win))
        else:
            msg = payload.get("error") or payload.get("message", "Registration failed")
            self._safe_call(lambda: messagebox.showerror("Error", msg, parent=self._login_win))

    def _on_private_msg(self, msg_type, seq, payload):
        if payload.get("_ack"):
            self._apply_message_ack(seq, payload)
            return
        if payload.get("from_id") == self._user_id:
            return
        sender = payload.get("from_username", payload.get("sender", f"User#{payload.get('from_id', '')}"))
        msg = {
            "type": "private", "sender": sender,
            "content": payload.get("content", ""),
            "msg_id": str(payload.get("msg_id", "")),
            "timestamp": payload.get("timestamp", _now()),
            "from_id": payload.get("from_id", 0),
            "target_id": payload.get("from_id", 0),
        }
        self._append_and_store(msg)
        if sender == self._current_target:
            self._safe_call(lambda: self._display_message(msg))

    def _on_group_msg(self, msg_type, seq, payload):
        if payload.get("_ack"):
            self._apply_message_ack(seq, payload)
            return
        if payload.get("from_id") == self._user_id:
            return
        sender = payload.get("from_username", payload.get("sender", f"User#{payload.get('from_id', '')}"))
        gid = str(payload.get("group_id", ""))
        msg = {
            "type": "group", "group_id": gid,
            "group_name": self._groups.get(gid, f"Group#{gid}"),
            "sender": sender, "content": payload.get("content", ""),
            "msg_id": str(payload.get("msg_id", "")),
            "timestamp": payload.get("timestamp", _now()),
            "from_id": payload.get("from_id", 0),
            "target_id": gid,
        }
        self._append_and_store(msg)
        if gid == self._current_target and self._chat_type == "group":
            self._safe_call(lambda: self._display_message(msg))

    def _on_status_update(self, msg_type, seq, payload):
        username = payload.get("username", "")
        uid = payload.get("user_id", 0)
        is_online = payload.get("is_online", False)
        if is_online:
            self._online_users[username] = uid
        else:
            self._online_users.pop(username, None)
        self._safe_call(self._refresh_contacts)

    def _on_ai_resp(self, msg_type, seq, payload):
        content = payload.get("content", "")
        if content:
            self._safe_call(lambda: self._display_message({
                "type": "system", "content": f"[AI] {content}"}))

    def _on_content_warn(self, msg_type, seq, payload):
        self._safe_call(lambda: self._display_message({
            "type": "system", "content": f"[WARN] {payload.get('message', 'Content warning')}"}))

    def _on_error(self, msg_type, seq, payload):
        self._safe_call(lambda: self._display_message({
            "type": "system", "content": f"[Error {payload.get('code', -1)}] {payload.get('message', '')}"}))

    def _on_recall(self, msg_type, seq, payload):
        if payload.get("success") is False:
            err = payload.get("error") or payload.get("message", "recall failed")
            self._safe_call(lambda: self._display_message({
                "type": "system", "content": f"Recall failed: {err}"}))
            return
        msg_id = str(payload.get("msg_id", ""))
        self._mark_recalled(msg_id)
        mid = msg_id[:8]
        self._safe_call(lambda: self._display_message({
            "type": "system", "content": f"Message {mid}... was recalled"}))

    def _on_history(self, msg_type, seq, payload):
        history = payload.get("messages", [])
        for m in history:
            self._messages.append(m)
        def show_history():
            self._display_message({
                "type": "system", "content": f"Loaded {len(history)} history messages"})
            for m in history:
                kind = "group" if payload.get("type") == "group" or m.get("group_id") else "private"
                self._display_message({
                    "type": kind,
                    "sender": self._history_sender(m),
                    "content": "[已撤回]" if m.get("recalled") or m.get("is_recalled") else m.get("content", ""),
                    "group_id": str(m.get("group_id", "")),
                })
        self._safe_call(show_history)

    def _on_online_users(self, msg_type, seq, payload):
        users = payload.get("users", [])
        self._online_users = {}
        for u in users:
            uid = u.get("id", 0)
            name = u.get("username", f"User#{uid}")
            self._online_users[name] = uid
        self._safe_call(self._refresh_contacts)

    def _on_group_create_resp(self, msg_type, seq, payload):
        if payload.get("success"):
            gid = str(payload.get("group_id", ""))
            name = payload.get("name", "")
            self._groups[gid] = name
            self._safe_call(self._refresh_contacts)
            self._safe_call(lambda: self._display_message({
                "type": "system", "content": f"Group '{name}' created (ID: {gid})"}))
        else:
            err = payload.get("error") or payload.get("message", "")
            self._safe_call(lambda: self._display_message({
                "type": "system", "content": f"Create group failed: {err}"}))

    def _on_group_join_resp(self, msg_type, seq, payload):
        if payload.get("success"):
            gid = str(payload.get("group_id", ""))
            name = payload.get("name", gid)
            self._groups[gid] = name
            self._safe_call(self._refresh_contacts)
            self._safe_call(lambda: self._display_message({
                "type": "system", "content": f"Joined group '{name}'"}))
        else:
            err = payload.get("error") or payload.get("message", "")
            self._safe_call(lambda: self._display_message({
                "type": "system", "content": f"Join failed: {err}"}))

    def _on_group_leave_resp(self, msg_type, seq, payload):
        gid = str(payload.get("group_id", ""))
        if payload.get("success"):
            self._groups.pop(gid, None)
            self._safe_call(self._refresh_contacts)
            self._safe_call(lambda: self._display_message({
                "type": "system", "content": f"Left group {gid}"}))
        else:
            err = payload.get("error") or payload.get("message", "")
            self._safe_call(lambda: self._display_message({
                "type": "system", "content": f"Leave failed: {err}"}))
        if self._current_target == gid and self._chat_type == "group":
            self._current_target = None
            self._chat_type = "private"

    def _on_file_init(self, msg_type, seq, payload):
        status = payload.get("status", "")
        if status != "completed":
            return
        file_id = payload.get("file_id", "")
        from_id = payload.get("from_id", 0)
        filename = payload.get("filename", "unknown")
        filesize = payload.get("filesize", 0)
        sender = f"User#{from_id}"
        for name, uid in self._online_users.items():
            if uid == from_id:
                sender = name
                break
        self._safe_call(lambda: self._display_message({
            "type": "system",
            "content": f"Incoming file from {sender}: {filename} ({filesize} bytes)"}))
        threading.Thread(target=self._gui_download_file,
                         args=(file_id, filename, filesize), daemon=True).start()

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

    def _gui_download_file(self, file_id, filename, filesize):
        CHUNK_SIZE = 64 * 1024
        state = {"data": {}, "remaining": filesize,
                 "event": threading.Event(), "chunk_size": CHUNK_SIZE}
        self._dl_state[file_id] = state
        for offset in range(0, filesize, CHUNK_SIZE):
            self.handler.request_file_chunk(file_id, offset)
        if not state["event"].wait(timeout=30):
            self._safe_call(lambda: self._display_message({
                "type": "system", "content": f"File download timed out: {filename}"}))
            self._dl_state.pop(file_id, None)
            return
        dest = os.path.join(self._download_dir, filename)
        try:
            os.makedirs(self._download_dir, exist_ok=True)
            with open(dest, "wb") as f:
                for offset in sorted(state["data"].keys()):
                    f.write(state["data"][offset])
            self._safe_call(lambda: self._display_message({
                "type": "system", "content": f"Downloaded: {filename} ({filesize} bytes)"}))
        except Exception as e:
            self._safe_call(lambda: self._display_message({
                "type": "system", "content": f"Download failed: {e}"}))
        self._dl_state.pop(file_id, None)

    def _on_disconnected(self):
        self._safe_call(lambda: self._display_message({
            "type": "system", "content": "Disconnected. Reconnecting..."}))

    def _on_reconnected(self):
        self._safe_call(lambda: self._display_message({
            "type": "system", "content": "Reconnected."}))
        if self._username and self._password_hash:
            self.handler.send_login(self._username, self._password_hash)

    def _transition_to_main(self):
        """登录成功后从登录窗口切换到主窗口（在同一个 after 中执行）"""
        if self._login_win:
            try:
                self._login_win.destroy()
            except tk.TclError:
                pass
            self._login_win = None
        if self._username and self._user_id:
            self._online_users[self._username] = self._user_id
        self.handler.request_online_users()
        self._build_main_window()

    # =============================================================
    # 登录窗口
    # =============================================================

    def _show_login(self):
        self._login_win = tk.Tk()
        self._login_win.title("Chat System - Login")
        self._login_win.geometry("400x320")
        self._login_win.resizable(False, False)

        frame = ttk.Frame(self._login_win, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Chat System v1.0",
                  font=("Arial", 18, "bold")).pack(pady=10)
        ttk.Label(frame, text="Distributed Instant Messaging",
                  font=("Arial", 9)).pack()

        ttk.Label(frame, text="Username:").pack(anchor=tk.W, pady=(15, 0))
        username_var = tk.StringVar()
        ttk.Entry(frame, textvariable=username_var).pack(fill=tk.X)

        ttk.Label(frame, text="Password:").pack(anchor=tk.W, pady=(5, 0))
        password_var = tk.StringVar()
        ttk.Entry(frame, textvariable=password_var, show="*").pack(fill=tk.X)

        status_var = tk.StringVar(value=f"Server: {self.host}:{self.port}")
        ttk.Label(frame, textvariable=status_var,
                  font=("Arial", 8), foreground="gray").pack(pady=(5, 0))

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(pady=20)

        def do_login():
            u, p = username_var.get().strip(), password_var.get().strip()
            if not u or not p:
                messagebox.showwarning("Warning", "Username and password required",
                                       parent=self._login_win)
                return
            if not self.conn.is_connected:
                messagebox.showerror("Error", "Not connected to server",
                                     parent=self._login_win)
                return
            self._username = u
            self._password_hash = p
            self.handler.send_login(u, p)

        def do_register():
            u, p = username_var.get().strip(), password_var.get().strip()
            if not u or not p:
                messagebox.showwarning("Warning", "Username and password required",
                                       parent=self._login_win)
                return
            if not self.conn.is_connected:
                messagebox.showerror("Error", "Not connected to server",
                                     parent=self._login_win)
                return
            self._password_hash = p
            self.handler.send_register(u, p)

        ttk.Button(btn_frame, text="Login", command=do_login).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Register", command=do_register).pack(side=tk.LEFT, padx=5)

        self._login_win.protocol("WM_DELETE_WINDOW", self._on_closing)
        self._login_win.mainloop()

    # =============================================================
    # 主窗口
    # =============================================================

    def _build_main_window(self):
        self._main_win = tk.Tk()
        self._main_win.title(f"Chat System - {self._username}")
        self._main_win.geometry("900x600")
        self._main_win.minsize(700, 400)

        menubar = tk.Menu(self._main_win)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Send File...", command=self._menu_send_file)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_closing)
        menubar.add_cascade(label="File", menu=file_menu)

        group_menu = tk.Menu(menubar, tearoff=0)
        group_menu.add_command(label="Create Group...", command=self._menu_create_group)
        group_menu.add_command(label="Join Group...", command=self._menu_join_group)
        group_menu.add_command(label="Leave Group...", command=self._menu_leave_group)
        menubar.add_cascade(label="Group", menu=group_menu)

        chat_menu = tk.Menu(menubar, tearoff=0)
        chat_menu.add_command(label="Online Users", command=self._menu_online_users)
        chat_menu.add_command(label="Load History", command=self._menu_history)
        chat_menu.add_command(label="Recall Last Sent", command=self._menu_recall_last)
        menubar.add_cascade(label="Chat", menu=chat_menu)

        self._main_win.config(menu=menubar)

        paned = ttk.PanedWindow(self._main_win, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left_frame = ttk.Frame(paned, width=220)
        paned.add(left_frame, weight=0)
        ttk.Label(left_frame, text="Contacts & Groups",
                  font=("Arial", 11, "bold")).pack(pady=5)

        self._contact_tree = ttk.Treeview(left_frame, columns=("status",), show="tree")
        self._contact_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self._contact_tree.bind("<<TreeviewSelect>>", self._on_contact_select)

        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=1)

        self._chat_title = ttk.Label(right_frame, text="Select a contact or group",
                                     font=("Arial", 12, "bold"))
        self._chat_title.pack(pady=5)

        self._msg_area = scrolledtext.ScrolledText(
            right_frame, state=tk.DISABLED, wrap=tk.WORD, font=("Arial", 10))
        self._msg_area.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self._msg_area.tag_config("me", foreground="green")
        self._msg_area.tag_config("other", foreground="blue")
        self._msg_area.tag_config("system", foreground="gray")
        self._msg_area.tag_config("group", foreground="purple")

        input_frame = ttk.Frame(right_frame)
        input_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        self._msg_input = ttk.Entry(input_frame)
        self._msg_input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._msg_input.bind("<Return>", self._on_send)

        ttk.Button(input_frame, text="Send", command=self._on_send_click).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(input_frame, text="File", command=self._menu_send_file).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(input_frame, text="@AI", command=self._on_ai_click).pack(side=tk.RIGHT, padx=(5, 0))

        self._status_bar = ttk.Label(
            self._main_win, text=f"Connected | {self.host}:{self.port}",
            relief=tk.SUNKEN, anchor=tk.W)
        self._status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        self._refresh_contacts()
        self._poll_status()

        self._main_win.protocol("WM_DELETE_WINDOW", self._on_closing)
        self._main_win.mainloop()

    # =============================================================
    # 联系人/群组
    # =============================================================

    def _refresh_contacts(self):
        if not self._contact_tree:
            return
        tree = self._contact_tree
        tree.delete(*tree.get_children())

        online_id = tree.insert("", tk.END, text=f"Online ({len(self._online_users)})",
                                open=True, tags=("header",))
        for username in sorted(self._online_users.keys()):
            tree.insert(online_id, tk.END, text=username, values=("online",), tags=("user",))

        group_id = tree.insert("", tk.END, text=f"Groups ({len(self._groups)})",
                               open=True, tags=("header",))
        for gid, gname in self._groups.items():
            tree.insert(group_id, tk.END, text=f"# {gname} ({gid})",
                        values=("group",), tags=("group",))

    def _poll_status(self):
        """定时刷新状态栏（不主动请求在线用户，避免过于频繁）"""
        if self._main_win and self._main_win.winfo_exists():
            status = "CONNECTED" if self.conn.is_connected else "DISCONNECTED"
            self._status_bar.config(
                text=f"{status} | {self.host}:{self.port} | "
                     f"Online: {len(self._online_users)} | Groups: {len(self._groups)}")
            self._main_win.after(3000, self._poll_status)

    def _on_contact_select(self, event):
        selection = self._contact_tree.selection()
        if not selection:
            return
        item = self._contact_tree.item(selection[0])
        parent_id = self._contact_tree.parent(selection[0])
        if not parent_id:
            return

        text = item.get("text", "")
        values = item.get("values", [])

        if "group" in values:
            gid = text.split("(")[-1].rstrip(")")
            self._current_target = gid
            self._current_target_id = None
            self._chat_type = "group"
            self._chat_title.config(text=f"Group: {text}")
        else:
            self._current_target = text
            self._current_target_id = self._online_users.get(text)
            self._chat_type = "private"
            self._chat_title.config(text=f"Chat with {text}")

        self._msg_area.config(state=tk.NORMAL)
        self._msg_area.delete(1.0, tk.END)
        history = [
            m for m in self._messages
            if m.get("type") == self._chat_type
            and (
                m.get("sender") == self._current_target
                or m.get("receiver") == self._current_target
                or str(m.get("target_id", "")) == str(self._current_target_id or self._current_target)
                or m.get("group_id") == self._current_target
            )
        ]
        for msg in history[-50:]:
            self._display_message(msg)
        self._msg_area.config(state=tk.DISABLED)

    # =============================================================
    # 消息显示
    # =============================================================

    def _display_message(self, msg: dict):
        if not self._msg_area:
            return
        self._msg_area.config(state=tk.NORMAL)

        t = msg.get("type", "")
        if t == "system":
            self._msg_area.insert(tk.END, f">>> {msg.get('content', '')}\n", "system")
        else:
            sender = msg.get("sender", "")
            content = msg.get("content", "")
            tag = "me" if sender == self._username else "other"
            prefix = f"[{sender}]" if t == "group" else f"{sender}:"
            self._msg_area.insert(tk.END, f"{prefix} {content}\n", tag)

        self._msg_area.see(tk.END)
        self._msg_area.config(state=tk.DISABLED)

    # =============================================================
    # 发送
    # =============================================================

    def _on_send(self, event=None):
        self._on_send_click()

    def _on_send_click(self):
        text = self._msg_input.get().strip()
        if not text or not self._current_target:
            return
        self._msg_input.delete(0, tk.END)

        if text.strip().upper().startswith("@AI"):
            query = text[3:].strip()
            if query and self._user_id:
                gid = int(self._current_target) if self._chat_type == "group" else 0
                self.handler.send_ai_query(self._user_id, gid, query)
                self._display_message({
                    "type": "system", "content": f"AI query sent: {query[:50]}..."})
            return

        if self._chat_type == "group":
            gid = self._current_target
            result = self.handler.send_group_msg(self._user_id or 0, int(gid), text)
            local_msg_id = str(result.get("client_msg_id", "")) if result else ""
            msg = {
                "type": "group", "group_id": str(gid), "target_id": str(gid),
                "sender": self._username or "You", "content": text,
                "local_msg_id": local_msg_id, "msg_id": local_msg_id,
                "timestamp": _now(), "status": "pending",
            }
            self._append_and_store(msg)
            self._remember_pending(result, msg)
            self._display_message(msg)
        else:
            target_id = self._current_target_id
            if not target_id:
                self._display_message({
                    "type": "system", "content": f"User '{self._current_target}' not online"})
                return
            result = self.handler.send_private_msg(self._user_id or 0, target_id, text)
            local_msg_id = str(result.get("client_msg_id", "")) if result else ""
            msg = {
                "type": "private", "sender": self._username or "You",
                "receiver": self._current_target, "target_id": target_id,
                "content": text, "local_msg_id": local_msg_id,
                "msg_id": local_msg_id, "timestamp": _now(),
                "status": "pending",
            }
            self._append_and_store(msg)
            self._remember_pending(result, msg)
            self._display_message(msg)

    def _on_ai_click(self):
        text = self._msg_input.get().strip()
        if not text:
            text = simpledialog.askstring("@AI", "Ask AI:", parent=self._main_win)
            if not text:
                return
        else:
            self._msg_input.delete(0, tk.END)

        if self._user_id:
            gid = int(self._current_target) if self._chat_type == "group" else 0
            self.handler.send_ai_query(self._user_id, gid, text)

    # =============================================================
    # 菜单操作
    # =============================================================

    def _menu_send_file(self):
        target = self._current_target
        if not target:
            messagebox.showinfo("Info", "Select a contact first", parent=self._main_win)
            return
        target_id = self._current_target_id
        if not target_id:
            messagebox.showinfo("Info", "User not online", parent=self._main_win)
            return
        filepath = filedialog.askopenfilename(title="Select file to send")
        if not filepath:
            return

        filesize = os.path.getsize(filepath)
        file_id = str(uuid.uuid4())
        filename = os.path.basename(filepath)

        self.handler.send_file_init(self._user_id or 0, target_id, filename, filesize, file_id)
        self._display_message({"type": "system", "content": f"Sending: {filename} ({filesize} bytes)"})

        # 后台发送文件块，不阻塞 GUI
        threading.Thread(target=self._send_file_worker,
                         args=(filepath, file_id, filesize, filename), daemon=True).start()

    def _send_file_worker(self, filepath, file_id, filesize, filename):
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
        self._safe_call(lambda: self._display_message({
            "type": "system", "content": f"Sent: {filename}"}))

    def _menu_create_group(self):
        name = simpledialog.askstring("Create Group", "Group name:", parent=self._main_win)
        if name:
            self.handler.group_create(name, self._user_id or 0)

    def _menu_join_group(self):
        gid = simpledialog.askstring("Join Group", "Group ID (number):", parent=self._main_win)
        if gid:
            try:
                self.handler.group_join(int(gid), self._user_id or 0)
            except ValueError:
                messagebox.showerror("Error", "Group ID must be a number", parent=self._main_win)

    def _menu_leave_group(self):
        gid = simpledialog.askstring("Leave Group", "Group ID (number):", parent=self._main_win)
        if gid:
            try:
                self.handler.group_leave(int(gid), self._user_id or 0)
            except ValueError:
                messagebox.showerror("Error", "Group ID must be a number", parent=self._main_win)

    def _menu_online_users(self):
        self.handler.request_online_users()

    def _menu_history(self):
        target = self._current_target
        if target:
            ttype = "group" if self._chat_type == "group" else "private"
            target_id = int(target) if ttype == "group" else self._current_target_id
            if target_id is None:
                self._display_message({
                    "type": "system", "content": f"User '{target}' not online"})
                return
            self.handler.request_history(ttype, target_id)
        else:
            messagebox.showinfo("Info", "Select a contact first", parent=self._main_win)

    def _menu_recall_last(self):
        if not self._last_sent_msg_id:
            self._display_message({
                "type": "system",
                "content": "No confirmed sent message can be recalled yet",
            })
            return
        self.handler.send_recall(self._last_sent_msg_id)

    # =============================================================
    # 关闭
    # =============================================================

    def _on_closing(self):
        self.conn.close()
        for win in (self._main_win, self._login_win):
            if win:
                try:
                    win.destroy()
                except tk.TclError:
                    pass

    # =============================================================
    # 入口
    # =============================================================

    def run(self):
        if not self.conn.connect(self.host, self.port):
            tk.Tk().withdraw()
            messagebox.showerror("Connection Error",
                                 f"Cannot connect to {self.host}:{self.port}\n"
                                 "Make sure the server is running.")
            return

        self._show_login()
