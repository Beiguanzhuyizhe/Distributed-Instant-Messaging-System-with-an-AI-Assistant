import shutil
import uuid
from pathlib import Path


RUNTIME_ROOT = Path(__file__).resolve().parent.parent / ".test_runtime"


def make_runtime_dir(prefix: str) -> Path:
    """在项目内创建测试临时目录，避开当前机器系统 Temp 的权限异常。"""
    RUNTIME_ROOT.mkdir(exist_ok=True)
    path = RUNTIME_ROOT / f"{prefix}{uuid.uuid4().hex}"
    path.mkdir()
    return path


def remove_runtime_dir(path: Path):
    shutil.rmtree(path, ignore_errors=True)
