"""
客户端 GUI 界面 — 基于 pywebview (Edge Chromium) 的现代化桌面聊天客户端

使用 WebView 渲染 React 前端，通过 js_api 桥接 Python 后端。
"""

import os
import tkinter as tk
from tkinter import messagebox

import webview

from connection import ChatConnection
from message_handler import MessageHandler
from message_store import MessageStore
from p2p import P2PClient
from web_bridge import WebBridge


class ChatGUI:
    """基于 pywebview 的现代化图形化聊天客户端"""

    def __init__(self, host=None, port=None):
        from config import Config
        self.host = host or Config.SERVER_HOST
        self.port = port or Config.SERVER_PORT

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
            title="Chat System",
            url=self._index_html,
            js_api=self._bridge,
            width=1100,
            height=720,
            resizable=True,
        )

        webview.start(debug=False)

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
