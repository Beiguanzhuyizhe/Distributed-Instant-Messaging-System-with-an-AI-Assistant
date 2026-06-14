import shutil
import tempfile
import uuid
from pathlib import Path


PROJECT_RUNTIME_ROOT = Path(__file__).resolve().parent.parent / ".test_runtime"
SYSTEM_RUNTIME_ROOT = Path(tempfile.gettempdir()) / "chat_demo_test_runtime"


def _runtime_root() -> Path:
    """优先使用系统 Temp 下的固定目录，避开当前 D 盘工作区 SQLite I/O 异常。"""
    for root in (SYSTEM_RUNTIME_ROOT, PROJECT_RUNTIME_ROOT):
        try:
            root.mkdir(exist_ok=True)
            return root
        except OSError:
            continue
    PROJECT_RUNTIME_ROOT.mkdir(exist_ok=True)
    return PROJECT_RUNTIME_ROOT


def make_runtime_dir(prefix: str) -> Path:
    """创建独立测试目录，不依赖 pytest 的 tmp_path/tmpdir fixture。"""
    path = _runtime_root() / f"{prefix}{uuid.uuid4().hex}"
    path.mkdir()
    return path


def remove_runtime_dir(path: Path):
    shutil.rmtree(path, ignore_errors=True)
