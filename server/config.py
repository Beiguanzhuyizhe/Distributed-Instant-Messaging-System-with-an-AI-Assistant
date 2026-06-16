"""
服务端配置模块
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


BIGMODEL_API_BASE = "https://open.bigmodel.cn/api/paas/v4"
BIGMODEL_MODEL = "glm-4-flash-250414"
DASHSCOPE_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DASHSCOPE_MODEL = "qwen-turbo"

_LOCAL_ENV_CACHE = None


def _parse_env_file(path: Path) -> dict:
    values = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _load_local_env() -> dict:
    global _LOCAL_ENV_CACHE
    if _LOCAL_ENV_CACHE is not None:
        return _LOCAL_ENV_CACHE

    project_root = Path(__file__).resolve().parents[1]
    candidates = []
    for path in (Path.cwd() / ".env", project_root / ".env"):
        if path not in candidates:
            candidates.append(path)

    merged = {}
    for path in candidates:
        if path.is_file():
            merged.update(_parse_env_file(path))
    _LOCAL_ENV_CACHE = merged
    return _LOCAL_ENV_CACHE


def _env(name: str) -> str:
    return os.environ.get(name) or _load_local_env().get(name, "")


def _env_has_dashscope_only() -> bool:
    return not _env("BIGMODEL_API_KEY") and bool(_env("DASHSCOPE_API_KEY"))


def _default_ai_api_key() -> str:
    return _env("BIGMODEL_API_KEY") or _env("DASHSCOPE_API_KEY") or ""


def _default_ai_model() -> str:
    if _env("AI_MODEL"):
        return _env("AI_MODEL")
    return DASHSCOPE_MODEL if _env_has_dashscope_only() else BIGMODEL_MODEL


def _default_ai_api_base() -> str:
    if _env("AI_API_BASE"):
        return _env("AI_API_BASE")
    return DASHSCOPE_API_BASE if _env_has_dashscope_only() else BIGMODEL_API_BASE


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

    # AI 服务：BIGMODEL_API_KEY 优先；仅配置 DASHSCOPE_API_KEY 时默认切到 DashScope
    ai_api_key: str = field(default_factory=_default_ai_api_key)
    ai_model: str = field(default_factory=_default_ai_model)
    ai_api_base: str = field(default_factory=_default_ai_api_base)

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
