"""
客户端配置模块
"""

import os


class Config:
    # === 服务器连接 ===
    SERVER_HOST = os.getenv("CHAT_SERVER_HOST", "127.0.0.1")
    SERVER_PORT = int(os.getenv("CHAT_SERVER_PORT", "8888"))

    # === 网络 ===
    BUFFER_SIZE = 4096
    HEARTBEAT_INTERVAL = 25   # 客户端心跳间隔（秒，略短于服务端超时）
    CONN_TIMEOUT = 10         # 连接超时（秒）

    # === 消息 ===
    MAX_PAYLOAD_SIZE = 1 << 20
    RECALL_WINDOW = 120       # 消息撤回时间窗口（2 分钟）

    # === 文件 ===
    FILE_CHUNK_SIZE = 64 * 1024
    FILE_DOWNLOAD_DIR = os.getenv("CHAT_DOWNLOAD_DIR", os.path.join(
        os.path.dirname(__file__), "..", "downloads"
    ))
    MAX_FILE_SIZE = 100 * (1 << 20)

    # === 本地存储 ===
    MESSAGE_STORE_DIR = os.getenv("CHAT_MSG_STORE_DIR", os.path.join(
        os.path.dirname(__file__), "..", "message_store"
    ))

    # === 重连 ===
    RECONNECT_DELAY_MIN = 1     # 最小重连延迟（秒）
    RECONNECT_DELAY_MAX = 30    # 最大重连延迟（秒）
    MAX_RECONNECT_ATTEMPTS = 5  # 最大重连尝试次数

    # === UI ===
    UI_THEME = "default"                       # 界面主题 (default / dark / light)
    UI_FONT_SIZE = 10                           # 字体大小
    GUI_WINDOW_WIDTH = 900                      # GUI 窗口宽度
    GUI_WINDOW_HEIGHT = 600                     # GUI 窗口高度
    CLI_REFRESH_RATE = 4                        # CLI 刷新率（帧/秒）
    CLI_MAX_MESSAGES = 100                      # CLI 最大显示消息数
    CLI_SIDEBAR_WIDTH = 28                      # CLI 侧边栏宽度（字符数）
