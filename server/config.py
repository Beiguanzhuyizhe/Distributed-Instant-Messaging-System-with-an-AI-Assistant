"""
服务端配置模块
"""
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ServerConfig:
    # 服务器网络配置
    host: str = "0.0.0.0"
    tcp_port: int = 8888
    udp_port: int = 8889  # P2P 打洞辅助端口

    # 数据库
    db_path: str = field(default_factory=lambda: os.path.join(
        os.path.dirname(__file__), "data", "chat.db"
    ))

    # 心跳检测
    heartbeat_interval: int = 30       # 心跳间隔 (秒)
    heartbeat_timeout: int = 90        # 心跳超时 (秒)

    # AI 服务 (默认智谱清言 BigModel API，也兼容通义千问)
    ai_api_key: str = field(default_factory=lambda: (
        os.environ.get("BIGMODEL_API_KEY") or
        os.environ.get("DASHSCOPE_API_KEY") or
        ""
    ))
    ai_model: str = field(default_factory=lambda: (
        os.environ.get("AI_MODEL", "glm-4-flash-250414")
    ))
    ai_api_base: str = field(default_factory=lambda: (
        os.environ.get("AI_API_BASE",
                       "https://open.bigmodel.cn/api/paas/v4")
    ))

    # 内容审核
    enable_content_moderation: bool = True
    moderate_on_server: bool = True

    # 文件传输
    file_chunk_size: int = 65536       # 64KB 每块
    max_file_size: int = 104857600     # 100MB
    file_storage_dir: str = field(default_factory=lambda: os.path.join(
        os.path.dirname(__file__), "file_storage"
    ))

    # 日志
    log_level: str = "INFO"
    log_file: Optional[str] = None

    # 消息
    recall_window: int = 120            # 消息撤回时间窗口（2 分钟 / 120 秒）

    # 并发
    max_connections: int = 200
    message_queue_size: int = 10000

    @classmethod
    def from_dict(cls, d: dict) -> "ServerConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def ensure_dirs(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        os.makedirs(self.file_storage_dir, exist_ok=True)
