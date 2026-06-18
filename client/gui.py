"""
客户端 GUI 界面 — 基于 pywebview (Edge Chromium) 的现代化桌面聊天客户端

使用 WebView 渲染 React 前端，通过 js_api 桥接 Python 后端。
"""

import json
import os
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import webview

from connection import ChatConnection
from message_handler import MessageHandler
from message_store import MessageStore
from p2p import P2PClient
from web_bridge import WebBridge


class ChatGUI:
    """基于 pywebview 的现代化图形化聊天客户端"""

    def __init__(
        self,
        host=None,
        port=None,
        demo_role=None,
        demo_user=None,
        demo_suffix=None,
        demo_password="demo_pass",
        demo_x=None,
        demo_y=None,
        demo_width=None,
        demo_height=None,
        demo_delay=1.0,
        demo_start_signal=None,
        demo_control_file=None,
        demo_ack_dir=None,
    ):
        from config import Config
        self.host = host or Config.SERVER_HOST
        self.port = port or Config.SERVER_PORT
        self.demo_role = demo_role
        self.demo_suffix = demo_suffix or "demo"
        self.demo_password = demo_password
        self.demo_user = demo_user or (f"demo_{demo_role}_{self.demo_suffix}" if demo_role else None)
        self.demo_x = demo_x
        self.demo_y = demo_y
        self.demo_width = demo_width
        self.demo_height = demo_height
        self.demo_delay = max(0.2, float(demo_delay or 1.0))
        self.demo_start_signal = str(demo_start_signal) if demo_start_signal else None
        self.demo_control_file = str(demo_control_file) if demo_control_file else None
        self.demo_ack_dir = str(demo_ack_dir) if demo_ack_dir else None
        self._demo_control_last_id = None

        # 后端模块（与旧版保持一致）
        self.conn = ChatConnection()
        self.handler = MessageHandler(self.conn)
        self.p2p = P2PClient()
        self.store = MessageStore()

        # Web 前端目录
        self._webui_dir = os.path.join(os.path.dirname(__file__), "webui")
        self._index_html = os.path.join(self._webui_dir, "index.html")

        # 下载目录
        self._download_dir = os.path.join(os.path.dirname(__file__), "downloads")
        os.makedirs(self._download_dir, exist_ok=True)

        # 桥接层（注册所有消息回调，管理状态，与 JS 通信）
        self._bridge = WebBridge(
            conn=self.conn,
            handler=self.handler,
            store=self.store,
            p2p=self.p2p,
            download_dir=self._download_dir,
        )

        # ---- 以下为向后兼容属性（供测试使用） ----
        self._current_target = None
        self._current_target_id = None
        self._chat_type = "private"

    def run(self):
        """启动 WebView 窗口（主入口）"""
        if not self.conn.connect(self.host, self.port):
            # 连接失败时显示 tkinter 错误框（在 WebView 启动前）
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "Connection Error",
                f"Cannot connect to {self.host}:{self.port}\n"
                "Make sure the server is running.",
            )
            root.destroy()
            return

        # 使用 Edge Chromium 创建 WebView 窗口
        self._window = webview.create_window(
            title=f"Chat System - {self.demo_role.title()}" if self.demo_role else "Chat System",
            url=self._index_html,
            js_api=self._bridge,
            width=self.demo_width or 1100,
            height=self.demo_height or 720,
            x=self.demo_x,
            y=self.demo_y,
            resizable=True,
        )

        webview.start(self._start_demo_script if self.demo_role else None, debug=False)

    # =============================================================
    # 录屏 demo 模式：真实 GUI + 真实服务端，只把人工操作改成脚本操作
    # =============================================================

    def _start_demo_script(self):
        self._demo_write_marker("state", "ready")
        if self.demo_control_file:
            threading.Thread(target=self._demo_control_worker, daemon=True).start()
        threading.Thread(target=self._demo_worker, daemon=True).start()

    def _demo_sleep(self, units=1.0):
        time.sleep(self.demo_delay * units)

    def _demo_notice(self, text, level="info", units=1.2):
        try:
            self._bridge._push_demo_notice(text, level=level, duration_ms=int(self.demo_delay * 1800 + 1800))
        except Exception:
            pass
        self._demo_sleep(units)

    def _demo_wait_until(self, predicate, timeout=30, interval=0.2):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return False

    def _demo_wait_for_start_signal(self):
        if not self.demo_start_signal:
            return
        self._demo_notice("窗口已就位：先调整大小和位置，确认后再启动正式演示", "warning", units=0.6)
        signal_path = Path(self.demo_start_signal)
        while not signal_path.exists():
            time.sleep(0.15)
        self._demo_notice("收到开始信号：进入正式演示", "success", units=0.5)

    def _demo_write_marker(self, kind, marker, extra=None):
        if not self.demo_ack_dir or not self.demo_role:
            return
        try:
            root = Path(self.demo_ack_dir)
            root.mkdir(parents=True, exist_ok=True)
            safe_kind = "".join(ch for ch in str(kind) if ch.isalnum() or ch in ("-", "_"))
            safe_marker = "".join(ch for ch in str(marker) if ch.isalnum() or ch in ("-", "_"))
            payload = {
                "role": self.demo_role,
                "kind": kind,
                "marker": marker,
                "timestamp": time.time(),
                "pid": os.getpid(),
            }
            if extra:
                payload.update(extra)
            target = root / f"{safe_kind}-{self.demo_role}-{safe_marker}.json"
            tmp = root / f".{safe_kind}-{self.demo_role}-{safe_marker}.tmp"
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(target)
        except Exception:
            pass

    def _demo_register_login(self):
        # 先注册再登录；若用户已存在，注册失败也不影响后续登录。
        self._demo_notice(f"{self.demo_user}：开始自动登录演示", "info", units=0.8)
        self._bridge.register(self.demo_user, self.demo_password)
        self._demo_sleep(1.5)
        self._bridge.login(self.demo_user, self.demo_password)
        self._demo_wait_until(lambda: self._bridge._logged_in, timeout=20)
        self._demo_notice(f"{self.demo_user}：登录成功，开始同步在线用户", "success")
        self._demo_write_marker("state", "logged_in")
        self._bridge.request_online_users()

    def _demo_username(self, role):
        return f"demo_{role}_{self.demo_suffix}"

    def _demo_wait_user(self, role, timeout=30):
        username = self._demo_username(role)

        def lookup():
            self._bridge.request_online_users()
            return self._bridge._online_users.get(username)

        if not self._demo_wait_until(lambda: lookup(), timeout=timeout):
            return None
        return self._bridge._online_users.get(username)

    def _demo_select_private(self, role):
        username = self._demo_username(role)
        user_id = self._demo_wait_user(role)
        if user_id:
            self._bridge.demo_select_chat("private", username, user_id)
            self._demo_sleep()
        return user_id

    def _demo_group_name(self):
        return f"DemoGroup_{self.demo_suffix}"

    def _demo_wait_group(self, joined=None, timeout=35):
        group_name = self._demo_group_name()

        def find_group():
            self._bridge.request_online_users()
            pools = []
            if joined is not False:
                pools.append(self._bridge._groups)
            if joined is not True:
                pools.append(self._bridge._available_groups)
            for pool in pools:
                for gid, value in pool.items():
                    name = value.get("name") if isinstance(value, dict) else value
                    if name == group_name:
                        return str(gid)
            return None

        holder = {"gid": None}

        def ready():
            holder["gid"] = find_group()
            return bool(holder["gid"])

        if self._demo_wait_until(ready, timeout=timeout):
            return holder["gid"]
        return None

    def _demo_select_group(self, group_id):
        if group_id:
            self._bridge.demo_select_chat("group", str(group_id), str(group_id))
            self._demo_sleep()

    def _demo_file_path(self):
        root = Path(__file__).resolve().parents[1] / ".test_runtime" / "gui_demo_files"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{self.demo_role}_{self.demo_suffix}.txt"
        path.write_text(
            f"GUI demo file from {self.demo_user}\n"
            f"suffix={self.demo_suffix}\n",
            encoding="utf-8",
        )
        return str(path)

    def _demo_control_worker(self):
        control_path = Path(self.demo_control_file)
        while True:
            try:
                if control_path.is_file():
                    payload = json.loads(control_path.read_text(encoding="utf-8"))
                    cue_id = payload.get("id")
                    if cue_id and cue_id != self._demo_control_last_id:
                        self._demo_control_last_id = cue_id
                        text = str(payload.get("text", "")).strip()
                        if text:
                            self._bridge._push_demo_notice(
                                text,
                                level=str(payload.get("level", "info")),
                                duration_ms=int(payload.get("duration_ms", 2600)),
                            )
                        self._demo_write_marker(
                            "notice",
                            cue_id,
                            {"text": text, "level": str(payload.get("level", "info"))},
                        )
            except Exception:
                pass
            time.sleep(0.2)

    def _demo_worker(self):
        try:
            if self.demo_start_signal:
                self._demo_wait_for_start_signal()
            else:
                self._demo_sleep(2.5)
            self._demo_register_login()
            role = (self.demo_role or "").lower()
            if role == "alice":
                self._demo_alice()
            elif role == "bob":
                self._demo_bob()
            elif role == "carol":
                self._demo_carol()
            self._demo_write_marker("state", "script_done")
        except Exception as exc:
            self._demo_write_marker("state", "script_failed", {"error": str(exc)})
            try:
                self._bridge._push_msg({
                    "type": "system",
                    "content": f"[Demo script stopped] {exc}",
                    "timestamp": int(time.time()),
                })
            except Exception:
                pass

    def _demo_alice(self):
        bob_id = self._demo_select_private("bob")
        self._demo_notice("Alice：切到 Bob 私聊，发送第一条隔离验证消息", "info")
        if bob_id:
            self._bridge.send_private_msg(bob_id, "【测试场景】Alice -> Bob：私聊消息只应出现在 Bob 会话")
        self._demo_sleep(2.4)

        self._demo_notice("Alice：切到 AI Assistant，验证独立 AI 会话中的提问与回复归属", "info", units=1.7)
        self._bridge.demo_select_chat("ai", "AI Assistant", WebBridge.AI_USER_ID)
        self._demo_sleep(1.2)
        self._demo_notice("Alice：先提出问题，再等待 AI 回答；这里应能看到“用户提问 -> AI 回复”两步", "info", units=1.2)
        self._bridge.demo_send_ai_query("请用一句简短的话向演示观众打个招呼。", 0)
        self._demo_sleep(4.4)
        group_name = self._demo_group_name()
        self._demo_notice(f"Alice：创建群组 {group_name}，稍后等待 Bob / Carol 加入", "info")
        self._bridge.group_create(group_name)
        group_id = self._demo_wait_group(joined=True)
        self._demo_select_group(group_id)
        if group_id:
            self._demo_notice("Alice：先停留在群里，给 Bob / Carol 一点时间看到新群与未读变化", "info", units=1.2)
            self._demo_sleep(3.0)
            self._demo_notice("Alice：在群里发消息，验证群消息不进入私聊", "info")
            self._bridge.send_group_msg(int(group_id), "【测试场景】Alice 创建群聊，群消息只应在群聊中显示")
            self._demo_sleep(4.2)
            self._demo_notice("Alice：在群聊中触发 @AI 回复广播，其他群成员应在群会话看到回复", "info", units=1.1)
            self._bridge.demo_send_ai_query(
                "请用一句话说明这是群聊 AI 演示。",
                int(group_id),
                chat_type="group",
                target_id=str(group_id),
            )
            self._demo_sleep(4.6)
            self._demo_notice("Alice：向群里发送文件，观察 Bob 和 Carol 的下载提示", "info")
            self._bridge.demo_send_file(
                self._demo_file_path(),
                chat_type="group",
                target_id=str(group_id),
            )
            self._demo_sleep(3.8)
            if bob_id:
                self._demo_notice("Alice：切回 Bob 私聊，确认前面的私聊消息历史仍然保留", "success", units=1.3)
                self._bridge.demo_select_chat("private", self._demo_username("bob"), bob_id)
                self._demo_sleep(2.2)
        self._demo_notice("Alice：保持当前会话，稍后观察断线后的状态栏与自动恢复", "success", units=0.9)

    def _demo_bob(self):
        self._bridge.demo_select_chat("ai", "AI Assistant", WebBridge.AI_USER_ID)
        self._demo_notice("Bob：先停留在其他会话，观察 Alice 私聊是否只在侧栏出现未读红点", "info", units=1.3)
        self._demo_sleep(3.2)
        alice_id = self._demo_select_private("alice")
        self._demo_notice("Bob：打开 Alice 私聊并回复，验证未读红点消失且消息归属正确", "info", units=1.0)
        if alice_id:
            self._bridge.send_private_msg(alice_id, "【测试场景】Bob -> Alice：收到私聊并回复")
        self._demo_sleep(2.2)

        group_id = self._demo_wait_group(joined=False)
        if group_id:
            self._demo_notice("Bob：加入 Alice 创建的群组", "info")
            self._bridge.group_join(int(group_id))
            self._demo_wait_group(joined=True)
            self._demo_notice("Bob：先留在私聊里，观察新群消息与群未读提示是否只出现在群入口", "info", units=1.2)
            self._demo_sleep(3.6)
            self._demo_select_group(group_id)
            self._demo_notice("Bob：切进群聊后发消息，稍后再退群", "info")
            self._bridge.send_group_msg(int(group_id), "【测试场景】Bob 已加入群聊并发送群消息")
            self._demo_sleep(5.8)
            self._demo_notice("Bob：退出群组，后续不应再收到群消息", "warning")
            self._bridge.group_leave(int(group_id))
            self._demo_sleep(2.8)
            if alice_id:
                self._demo_notice("Bob：退群后切回 Alice 私聊，说明私聊功能仍然正常", "success", units=1.2)
                self._bridge.demo_select_chat("private", self._demo_username("alice"), alice_id)
                self._demo_sleep(1.8)
                self._bridge.send_private_msg(alice_id, "【测试场景】Bob 已退群，但私聊仍然可以正常使用")
        self._demo_notice("Bob：保持当前会话，稍后观察断线后的状态栏与自动恢复", "success", units=0.9)

    def _demo_carol(self):
        self._bridge.demo_select_chat("ai", "AI Assistant", WebBridge.AI_USER_ID)
        self._demo_notice("Carol：先停留在非群聊会话，便于观察后续群入口与未读变化", "info", units=1.1)
        self._demo_sleep(2.6)
        group_id = self._demo_wait_group(joined=False)
        if group_id:
            self._demo_notice("Carol：加入群组，验证群广播和群文件接收", "info")
            self._bridge.group_join(int(group_id))
            self._demo_wait_group(joined=True)
            self._demo_notice("Carol：先观察新群入口与未读提示，再切进群聊", "info", units=1.0)
            self._demo_sleep(3.0)
            self._demo_select_group(group_id)
            self._demo_notice("Carol：在群里发言，验证三人协同群聊与广播一致性", "info")
            self._bridge.send_group_msg(int(group_id), "【测试场景】Carol 也在群聊中收到广播并发言")
            self._demo_sleep(4.2)
            self._demo_notice("Carol：继续停留在群聊，便于观察群文件、群 AI 回复以及稍后的断线恢复", "info", units=1.2)

    # =============================================================
    # 向后兼容方法（供 test_client_player2.py 等测试使用）
    # 将旧式 GUI 操作委托给桥接层
    # =============================================================

    @property
    def _username(self):
        return self._bridge._username

    @_username.setter
    def _username(self, value):
        self._bridge._username = value

    @property
    def _user_id(self):
        return self._bridge._user_id

    @_user_id.setter
    def _user_id(self, value):
        self._bridge._user_id = value

    def _menu_history(self):
        """向后兼容：请求历史消息"""
        target = self._current_target
        if target:
            ttype = "group" if self._chat_type == "group" else "private"
            target_id = int(target) if ttype == "group" else self._current_target_id
            if target_id is not None:
                self.handler.request_history(ttype, target_id)

    def _menu_send_file(self):
        """向后兼容：发送文件（桩方法）"""
        pass

    def _menu_create_group(self):
        """向后兼容：创建群组（桩方法）"""
        pass

    def _menu_join_group(self):
        """向后兼容：加入群组（桩方法）"""
        pass

    def _menu_leave_group(self):
        """向后兼容：退出群组（桩方法）"""
        pass

    def _menu_online_users(self):
        """向后兼容：请求在线用户列表"""
        self.handler.request_online_users()

    def _menu_recall_last(self):
        """向后兼容：撤回最后一条消息（桩方法）"""
        pass

    def _display_message(self, msg: dict):
        """向后兼容：显示消息（WebView 模式下空操作）"""
        pass
