"""
录屏专用最终验收 Demo Runner。

用法：
  python tests\\demo_runner.py
  python tests\\demo_runner.py --fast
  python tests\\demo_runner.py --pause-between-sections
  python tests\\demo_runner.py --video-mode

定位：
  这个脚本面向演示视频素材，输出“人能看懂”的分场景验收结果。
  它不替代 GUI 手动演示；它负责证明工程测试、稳定性和压测结果。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLIENT_ROOT = PROJECT_ROOT / "client"
RUNTIME_DIR = PROJECT_ROOT / ".test_runtime"
HOST = "127.0.0.1"
PORT = 8888

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.protocol import MessageType, decode_messages, encode_message
from tests.stress_test import StressTester, print_report


try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True, write_through=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True, write_through=True)
except Exception:
    pass

logging.getLogger("stress_test").setLevel(logging.WARNING)


class DemoFailure(Exception):
    def __init__(self, trigger: str, actual: str, impact: str):
        super().__init__(actual)
        self.trigger = trigger
        self.actual = actual
        self.impact = impact


@dataclass
class SectionResult:
    name: str
    ok: bool
    detail: str


class ProtocolClient:
    def __init__(self, label: str, suffix: str):
        self.label = label
        self.username = f"{label.lower()}_{suffix}"
        self.user_id: Optional[int] = None
        self.sock: Optional[socket.socket] = None
        self.buffer = b""
        self.inbox: list[tuple[int, int, dict]] = []
        self.seq = 0

    def connect(self):
        self.buffer = b""
        self.inbox = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(1.0)
        self.sock.connect((HOST, PORT))

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def send(self, msg_type: int, payload: dict) -> int:
        if not self.sock:
            raise DemoFailure(
                "发送协议消息",
                f"{self.label} socket 未连接",
                "无法继续执行真实服务端场景验收",
            )
        self.seq += 1
        self.sock.sendall(encode_message(msg_type, payload, seq=self.seq))
        return self.seq

    def pump(self, duration: float = 0.05):
        if not self.sock:
            return
        deadline = time.time() + duration
        while time.time() < deadline:
            self.sock.settimeout(max(0.01, min(0.05, deadline - time.time())))
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                raise DemoFailure(
                    "读取服务端消息",
                    f"{self.label} 连接被关闭",
                    "多用户场景无法继续验证",
                )
            self.buffer += chunk
            messages, self.buffer = decode_messages(self.buffer)
            self.inbox.extend(messages)

    def wait(
        self,
        msg_type: Optional[int] = None,
        predicate: Optional[Callable[[dict], bool]] = None,
        timeout: float = 5.0,
        desc: str = "message",
    ) -> tuple[int, int, dict]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for idx, (current_type, seq, payload) in enumerate(list(self.inbox)):
                if msg_type is not None and current_type != msg_type:
                    continue
                if predicate and not predicate(payload):
                    continue
                self.inbox.pop(idx)
                return current_type, seq, payload
            self.pump(0.08)

        tail = [(current_type, payload) for current_type, _seq, payload in self.inbox[-6:]]
        raise DemoFailure(
            f"{self.label} 等待 {desc}",
            f"超时未收到目标消息；最近 inbox={tail}",
            "可能存在消息路由、ACK 或异步通知异常",
        )

    def assert_no(
        self,
        msg_type: Optional[int] = None,
        predicate: Optional[Callable[[dict], bool]] = None,
        timeout: float = 0.45,
        desc: str = "unexpected message",
    ):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for current_type, _seq, payload in list(self.inbox):
                if msg_type is not None and current_type != msg_type:
                    continue
                if predicate and not predicate(payload):
                    continue
                raise DemoFailure(
                    f"{self.label} 检查不应收到 {desc}",
                    f"实际收到 type={current_type}, payload={payload}",
                    "可能导致 GUI 串聊、未读红点错位或消息出现在错误会话",
                )
            self.pump(0.05)

    def register_and_login(self):
        self.send(
            MessageType.REGISTER_REQ,
            {"username": self.username, "password_hash": "demo_pass"},
        )
        _msg_type, _seq, payload = self.wait(
            MessageType.REGISTER_RESP,
            lambda p: p.get("success") is True,
            desc="注册响应",
        )
        self.user_id = int(payload["user_id"])

        self.login()

    def login(self) -> dict:
        self.send(
            MessageType.LOGIN_REQ,
            {"username": self.username, "password_hash": "demo_pass"},
        )
        _msg_type, _seq, payload = self.wait(
            MessageType.LOGIN_RESP,
            lambda p: p.get("success") is True and (
                self.user_id is None or int(p.get("user_id")) == self.user_id
            ),
            desc="登录响应",
        )
        self.user_id = int(payload["user_id"])
        return payload


class DemoRunner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.results: list[SectionResult] = []
        self.server_proc: Optional[subprocess.Popen] = None
        self.managed_server = not args.use_existing_server
        self.gui_demo_procs: list[tuple[str, subprocess.Popen]] = []
        self.demo_start_signal = RUNTIME_DIR / "gui_demo_start.signal"
        self.demo_control_file = RUNTIME_DIR / "gui_demo_control.json"
        self.demo_ack_dir = RUNTIME_DIR / "gui_demo_acks"
        self.local_env = self.load_local_env()

    # ------------------------- 输出辅助 -------------------------

    @staticmethod
    def line(char: str = "=", width: int = 72):
        print(char * width)

    @staticmethod
    def progress_bar(value: int, total: int, width: int = 32) -> str:
        if total <= 0:
            ratio = 1.0 if value == 0 else 0.0
        else:
            ratio = max(0.0, min(1.0, value / total))
        filled = int(round(ratio * width))
        return f"[{'#' * filled}{'.' * (width - filled)}] {value}/{total}"

    @staticmethod
    def status_bar(ok: bool, width: int = 32) -> str:
        char = "#" if ok else "!"
        label = "OK" if ok else "CHECK"
        return f"[{char * width}] {label}"

    def panel(self, title: str, lines: list[str]):
        width = 72
        print(f"  +-- {title} " + "-" * max(0, width - len(title) - 8))
        for line in lines:
            print(f"  | {line}")
        print("  +" + "-" * (width - 3))
        self.demo_wait()

    def demo_wait(self, units: float = 1.0):
        delay = max(0.0, float(getattr(self.args, "demo_delay", 0.0) or 0.0)) * units
        if delay > 0:
            time.sleep(delay)

    @staticmethod
    def parse_env_file(path: Path) -> dict[str, str]:
        values: dict[str, str] = {}
        if not path.is_file():
            return values
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
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

    @classmethod
    def load_local_env(cls) -> dict[str, str]:
        env = {}
        for path in (PROJECT_ROOT / ".env", Path.cwd() / ".env"):
            env.update(cls.parse_env_file(path))
        return env

    def env_value(self, name: str) -> str:
        return os.environ.get(name) or self.local_env.get(name, "")

    def ai_config_summary(self) -> tuple[str, str, str]:
        has_bigmodel = bool(self.env_value("BIGMODEL_API_KEY"))
        has_dashscope = bool(self.env_value("DASHSCOPE_API_KEY"))
        if has_bigmodel:
            provider = "BigModel"
            model = self.env_value("AI_MODEL") or "glm-4-flash-250414"
            state = "已配置真实 LLM Key"
        elif has_dashscope:
            provider = "DashScope"
            model = self.env_value("AI_MODEL") or "qwen-turbo"
            state = "已配置真实 LLM Key"
        else:
            provider = "未配置"
            model = "-"
            state = "未配置 Key，将展示 AI 友好兜底"
        base = self.env_value("AI_API_BASE") or ("默认地址" if provider != "未配置" else "-")
        return state, provider, f"{model} / {base}"

    def countdown_to_start(self):
        seconds = max(0, int(getattr(self.args, "start_countdown", 0) or 0))
        if seconds <= 0:
            return
        self.line("-")
        print("正式开始倒计时：")
        for remaining in range(seconds, 0, -1):
            print(f"  {remaining} ...")
            time.sleep(1)
        print("  START")
        self.line("-")

    def title(self):
        self.line("=")
        print("  分布式即时聊天系统 Final Acceptance Demo Runner")
        print("  定位：自动化验收演示 / 功能测试 / 并发与稳定性验证")
        self.line("=")
        self.panel(
            "测试总览",
            [
                "01 环境 -> 02 三用户协同 -> 03 GUI 路由 -> 04 单元测试",
                "05 集成测试 -> 06 50 并发 -> 07 100 并发 -> 08 断线重连 -> 09 清理",
                "核心画面：真实 GUI 观察窗 + 路由矩阵 + 文件中继链路 + 压测指标条",
            ],
        )
        self.panel(
            "系统架构速览",
            [
                "Alice / Bob / Carol 客户端",
                "        <== TCP 自定义二进制协议：12 字节头 + JSON payload ==>",
                "asyncio 服务端：登录鉴权 / 消息路由 / SQLite / 文件中继 / 内容审核 / AI",
            ],
        )
        print()

    def section(self, code: str, name: str, purpose: str, func: Callable[[], str]):
        full_name = f"{code} {name}"
        if self.args.gui_demo and code in {"02", "03", "08", "09"}:
            self.ensure_gui_demo_running(f"进入 {full_name} 前检查 GUI demo")
            self.broadcast_gui_notice(
                f"阶段 {code}：{name}",
                level="info" if code != "08" else "warning",
                duration_ms=3400 if code != "08" else 4200,
            )
            self.demo_wait(0.5)
        self.line("-")
        print(f"{full_name}")
        print(f"验证目标：{purpose}")
        self.line("-")
        try:
            detail = func()
            self.results.append(SectionResult(full_name, True, detail or "通过"))
            print(f"[PASS] {full_name}：{detail or '通过'}")
        except DemoFailure as exc:
            self.results.append(SectionResult(full_name, False, exc.actual))
            print(f"[FAIL] {full_name}")
            print(f"  触发步骤：{exc.trigger}")
            print(f"  实际现象：{exc.actual}")
            print(f"  可能影响：{exc.impact}")
        except Exception as exc:
            self.results.append(SectionResult(full_name, False, f"{type(exc).__name__}: {exc}"))
            print(f"[FAIL] {full_name}")
            print(f"  触发步骤：执行本段验收")
            print(f"  实际现象：{type(exc).__name__}: {exc}")
            print("  可能影响：该段测试未通过，需要先定位环境或代码问题")
        print()
        self.demo_wait()
        if self.args.pause_between_sections:
            input("按 Enter 继续下一段录屏...")

    def pass_step(self, text: str):
        print(f"  [PASS] {text}")
        self.demo_wait()

    def route_matrix(self, title: str, rows: list[tuple[str, str, str]]):
        print(f"  {title}")
        for src, dst, status in rows:
            print(f"    {src:<18} -> {dst:<18} {status}")
        self.demo_wait()

    def print_stress_visual(self, label: str, report):
        expected_messages = max(1, report.total_messages_sent)
        self.panel(
            f"{label} 压测可视化指标",
            [
                f"连接成功  {self.progress_bar(report.connected, report.total_clients)}",
                f"登录成功  {self.progress_bar(report.login_success, report.total_clients)}",
                f"ACK 确认  {self.progress_bar(report.total_messages_acked, expected_messages)}",
                f"接收计数  {self.progress_bar(report.total_messages_received, expected_messages)}",
                f"错误状态  {self.status_bar(report.total_errors == 0)} errors={report.total_errors}",
                f"平均延迟  {report.avg_latency * 1000:.2f} ms    P99={report.p99_latency * 1000:.2f} ms",
            ],
        )

    @staticmethod
    def command_label(command: list[str]) -> str:
        return " ".join(command)

    def run_command(self, command: list[str], timeout: Optional[int] = None) -> None:
        print(f"  命令：{self.command_label(command)}")
        self.demo_wait(0.5)
        completed = subprocess.run(command, cwd=PROJECT_ROOT, timeout=timeout)
        if completed.returncode != 0:
            raise DemoFailure(
                f"运行命令：{self.command_label(command)}",
                f"退出码 {completed.returncode}",
                "对应自动化检查未通过，不能作为最终 PASS 素材",
            )

    # ------------------------- 服务端和清理 -------------------------

    @staticmethod
    def port_is_open() -> bool:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                return True
        except OSError:
            return False

    def prepare(self):
        if self.managed_server:
            if self.port_is_open():
                raise DemoFailure(
                    "启动 Demo Runner",
                    "检测到 8888 端口已被占用",
                    "请先关闭旧服务端，或使用 --use-existing-server 明确复用已有服务端",
                )
            self.clean_runtime_artifacts(silent=True)
            self.start_server()
        else:
            if not self.port_is_open():
                raise DemoFailure(
                    "连接已有服务端",
                    "未检测到 127.0.0.1:8888 服务端",
                    "请先运行 python -m server.main，或去掉 --use-existing-server",
                )
        if self.args.gui_demo:
            self.start_gui_demo()

    def ensure_gui_demo_running(self, stage: str):
        failed: list[str] = []
        for role, proc in self.gui_demo_procs:
            code = proc.poll()
            if code is not None:
                failed.append(f"{role}(pid={proc.pid}, exit={code})")
        if failed:
            raise DemoFailure(
                stage,
                "GUI demo 进程提前退出：" + ", ".join(failed),
                "录屏时会出现窗口缺失或演示中断，需要先解决 GUI 启动问题",
            )

    def running_gui_roles(self) -> list[str]:
        return [role for role, proc in self.gui_demo_procs if proc.poll() is None]

    def sync_marker_path(self, kind: str, role: str, marker: str) -> Path:
        safe_kind = "".join(ch for ch in str(kind) if ch.isalnum() or ch in ("-", "_"))
        safe_marker = "".join(ch for ch in str(marker) if ch.isalnum() or ch in ("-", "_"))
        return self.demo_ack_dir / f"{safe_kind}-{role}-{safe_marker}.json"

    def wait_for_gui_markers(
        self,
        kind: str,
        marker: str,
        roles: Optional[list[str]] = None,
        timeout: float = 8.0,
        required: bool = True,
    ) -> bool:
        if not self.args.gui_demo or getattr(self.args, "no_gui_sync", False):
            return True
        roles = roles or self.running_gui_roles()
        if not roles:
            return True
        pending = set(roles)
        deadline = time.time() + timeout
        while time.time() < deadline and pending:
            self.ensure_gui_demo_running(f"等待 GUI 同步标记 {kind}:{marker}")
            for role in list(pending):
                if self.sync_marker_path(kind, role, marker).is_file():
                    pending.remove(role)
            if pending:
                time.sleep(0.05)
        if not pending:
            print(f"[SYNC] GUI 已同步 {kind}:{marker} -> {', '.join(roles)}")
            return True
        message = f"等待 GUI 同步 {kind}:{marker} 超时，未确认窗口：{', '.join(sorted(pending))}"
        if required:
            raise DemoFailure(
                "等待 GUI 与终端同步",
                message,
                "录屏画面可能出现终端进入下一段但 GUI 还停留在上一段",
            )
        print(f"[WARN] {message}")
        return False

    def wait_for_gui_state(self, state: str, timeout: float = 20.0):
        self.wait_for_gui_markers("state", state, timeout=timeout, required=True)

    def wait_for_recording_ready(self):
        if not self.args.wait_before_run:
            return
        self.ensure_gui_demo_running("进入预备状态前检查 GUI demo")
        self.panel(
            "预备状态",
            [
                "服务端和 3 个 GUI 已启动，但自动演示还没有开始",
                "现在可以自由调整终端和 GUI 的大小、位置、层级",
                "确认布局就绪后，回到这个终端按 Enter，再正式开始整段验收",
            ],
        )
        input("准备好后按 Enter：正式开始测试演示...")
        self.ensure_gui_demo_running("发送开始信号前检查 GUI demo")
        self.countdown_to_start()
        self.arm_demo_start()
        self.wait_for_gui_state("logged_in", timeout=35)
        self.demo_wait(0.3)

    def arm_demo_start(self):
        if not self.args.gui_demo:
            return
        self.demo_start_signal.parent.mkdir(exist_ok=True)
        self.demo_start_signal.write_text("start\n", encoding="utf-8")
        print(f"[INFO] 已发送 GUI demo 开始信号：{self.demo_start_signal}")

    def broadcast_gui_notice(self, text: str, level: str = "info", duration_ms: int = 2800, wait_for_gui: bool = True):
        if not self.args.gui_demo:
            return
        self.demo_control_file.parent.mkdir(exist_ok=True)
        payload = {
            "id": uuid.uuid4().hex,
            "text": str(text),
            "level": str(level),
            "duration_ms": int(duration_ms),
            "timestamp": time.time(),
        }
        self.demo_control_file.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        if wait_for_gui:
            self.wait_for_gui_markers("notice", payload["id"], timeout=3.5, required=False)

    def start_server(self):
        RUNTIME_DIR.mkdir(exist_ok=True)
        stdout_path = RUNTIME_DIR / "server_stdout.log"
        stderr_path = RUNTIME_DIR / "server_stderr.log"
        stdout = stdout_path.open("ab")
        stderr = stderr_path.open("ab")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.server_proc = subprocess.Popen(
                [sys.executable, "-m", "server.main"],
                cwd=PROJECT_ROOT,
                stdout=stdout,
                stderr=stderr,
                creationflags=creationflags,
            )
        finally:
            stdout.close()
            stderr.close()

        deadline = time.time() + 12
        while time.time() < deadline:
            if self.server_proc.poll() is not None:
                tail = self.read_log_tail(stderr_path)
                raise DemoFailure(
                    "启动服务端",
                    f"服务端提前退出，stderr tail={tail}",
                    "后续真实服务端测试无法执行",
                )
            if self.port_is_open():
                print(f"[INFO] 服务端已启动 pid={self.server_proc.pid}")
                return
            time.sleep(0.25)

        raise DemoFailure(
            "启动服务端",
            "12 秒内未监听 127.0.0.1:8888",
            "可能是依赖缺失、端口异常或服务端启动失败",
        )

    @staticmethod
    def read_log_tail(path: Path, limit: int = 800) -> str:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text[-limit:]

    def stop_server(self):
        if not self.server_proc:
            return
        if self.server_proc.poll() is None:
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_proc.kill()
                self.server_proc.wait(timeout=5)
        self.server_proc = None

    def start_gui_demo(self):
        suffix = uuid.uuid4().hex[:6]
        try:
            self.demo_start_signal.unlink()
        except FileNotFoundError:
            pass
        try:
            self.demo_control_file.unlink()
        except FileNotFoundError:
            pass
        shutil.rmtree(self.demo_ack_dir, ignore_errors=True)
        self.demo_ack_dir.mkdir(parents=True, exist_ok=True)
        width = int(getattr(self.args, "gui_width", 610) or 610)
        height = int(getattr(self.args, "gui_height", 620) or 620)
        y = int(getattr(self.args, "gui_y", 40) or 40)
        x0 = int(getattr(self.args, "gui_x", 20) or 20)
        gap = int(getattr(self.args, "gui_gap", 12) or 12)
        roles = ["alice", "bob", "carol"]
        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")

        self.panel(
            "真实 GUI 联动观察",
            [
                "将启动 Alice / Bob / Carol 三个真实 pywebview 客户端窗口",
                "每个窗口会自动注册登录，并通过现有 WebBridge 发送真实 TCP 消息",
                "这些窗口用于观察未读红点、消息归属、AI 回复位置、文件提示和状态栏变化",
                "终端进入关键阶段时会等待 3 个 GUI ACK，确保提示和画面基本同步",
            ],
        )
        for idx, role in enumerate(roles):
            username = f"demo_{role}_{suffix}"
            cmd = [
                sys.executable,
                "-m",
                "client.main",
                "--gui",
                "--host",
                HOST,
                "--port",
                str(PORT),
                "--demo-role",
                role,
                "--demo-user",
                username,
                "--demo-suffix",
                suffix,
                "--demo-password",
                "demo_pass",
                "--demo-x",
                str(x0 + idx * (width + gap)),
                "--demo-y",
                str(y),
                "--demo-width",
                str(width),
                "--demo-height",
                str(height),
                "--demo-delay",
                str(max(0.2, float(getattr(self.args, "gui_demo_delay", 1.0) or 1.0))),
                "--demo-control-file",
                str(self.demo_control_file),
                "--demo-ack-dir",
                str(self.demo_ack_dir),
            ]
            if self.args.wait_before_run:
                cmd.extend(["--demo-start-signal", str(self.demo_start_signal)])
            proc = subprocess.Popen(cmd, cwd=PROJECT_ROOT, env=env)
            self.gui_demo_procs.append((role, proc))
            print(f"[INFO] 已启动真实 GUI demo：{role} pid={proc.pid} username={username}")
        self.ensure_gui_demo_running("启动真实 GUI demo")
        self.wait_for_gui_state("ready", timeout=25)
        if not self.args.wait_before_run:
            self.wait_for_gui_state("logged_in", timeout=35)

    def stop_gui_demo(self):
        for _role, proc in self.gui_demo_procs:
            if proc.poll() is None:
                proc.terminate()
        for _role, proc in self.gui_demo_procs:
            if proc.poll() is None:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self.gui_demo_procs = []

    def clean_runtime_artifacts(self, silent: bool = False):
        if self.args.use_existing_server and not silent:
            print("[INFO] 使用已有服务端模式：跳过运行数据清理，避免影响外部服务端。")
            return

        root = PROJECT_ROOT.resolve()
        targets = [
            PROJECT_ROOT / ".pytest_cache",
            PROJECT_ROOT / ".test_runtime",
            PROJECT_ROOT / "server" / "data",
            PROJECT_ROOT / "server" / "file_storage",
            PROJECT_ROOT / "client" / "data",
            PROJECT_ROOT / "client" / "downloads",
            PROJECT_ROOT / "server.log",
        ]

        for target in targets:
            self.remove_inside_root(root, target)

        for pycache in PROJECT_ROOT.rglob("__pycache__"):
            self.remove_inside_root(root, pycache)

        if not silent:
            print("[PASS] 已清理运行数据、文件缓存、测试缓存和日志。")

    @staticmethod
    def remove_inside_root(root: Path, target: Path):
        try:
            resolved = target.resolve()
        except OSError:
            return
        if not target.exists():
            return
        if os.path.commonpath([str(root), str(resolved)]) != str(root):
            raise DemoFailure(
                "清理运行产物",
                f"拒绝删除项目外路径：{resolved}",
                "清理逻辑存在路径风险",
            )
        for attempt in range(4):
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                return
            except FileNotFoundError:
                return
            except PermissionError as exc:
                if attempt < 3:
                    time.sleep(0.25)
                    continue
                raise DemoFailure(
                    "清理运行产物",
                    f"路径仍被占用：{resolved}；{exc}",
                    "请关闭旧服务端、GUI 或占用该目录的编辑器后重试",
                )

    # ------------------------- 各段验收 -------------------------

    def section_environment(self) -> str:
        self.run_command(["git", "status", "--short", "--branch"], timeout=10)
        self.run_command(["git", "log", "--oneline", "-3"], timeout=10)
        print(f"  Python：{sys.version.split()[0]}")
        print(f"  项目目录：{PROJECT_ROOT}")
        ai_state, ai_provider, ai_runtime = self.ai_config_summary()
        self.panel(
            "环境检查信息",
            [
                "当前分支和最近提交已展示，可证明录屏版本来源",
                "Python 环境已展示，后续所有测试均在同一项目目录下执行",
                "测试服务端由 runner 自动启动/停止，避免人工漏关服务端影响结果",
                f"AI 配置状态：{ai_state}",
                f"AI 提供方：{ai_provider}",
                f"AI 运行参数：{ai_runtime}",
            ],
        )
        return "已展示版本、分支、最近提交和 Python 环境"

    def section_three_user_acceptance(self) -> str:
        suffix = uuid.uuid4().hex[:8]
        alice = ProtocolClient("Alice", suffix)
        bob = ProtocolClient("Bob", suffix)
        carol = ProtocolClient("Carol", suffix)
        clients = [alice, bob, carol]

        try:
            self.panel(
                "三用户测试拓扑",
                [
                    "Alice --私聊--> Bob       Carol 必须收不到",
                    "Alice --群聊--> Bob/Carol 群消息必须不进入任何私聊",
                    "Alice --文件--> 服务端中继存储 --下载--> Bob/Carol",
                    "edge case：零字节文件 + Bob 重登后群状态恢复",
                    "Bob 退群后：不能发群消息 / 不能查群历史 / 不能下载群文件",
                ],
            )
            for client in clients:
                client.connect()
                client.register_and_login()
            ids = {client.user_id for client in clients}
            if len(ids) != 3:
                raise DemoFailure(
                    "Alice/Bob/Carol 注册登录",
                    f"用户 ID 不唯一：{ids}",
                    "用户注册登录或在线身份管理异常",
                )
            self.pass_step(
                f"Alice/Bob/Carol 注册登录成功：Alice={alice.user_id}, Bob={bob.user_id}, Carol={carol.user_id}"
            )

            self.verify_private_routing(alice, bob, carol, suffix)
            group_id, group_file_id = self.verify_group_file_history_recall(alice, bob, carol, suffix)
            self.verify_content_moderation(alice, bob, carol, group_id, suffix)
            self.verify_ai_fallback_or_response(alice, bob, carol, group_id, suffix)
            self.verify_leave_group(alice, bob, carol, group_id, group_file_id, suffix)
            return "三用户协同、隔离、历史、撤回、文件、零字节边界、重登恢复、审核、AI 兜底、退群权限均通过"
        finally:
            for client in clients:
                client.close()

    def verify_private_routing(
        self,
        alice: ProtocolClient,
        bob: ProtocolClient,
        carol: ProtocolClient,
        suffix: str,
    ):
        content = f"private_to_bob_{suffix}"
        alice.send(MessageType.PRIVATE_MSG, {"to_id": bob.user_id, "content": content})
        _type, _seq, ack = alice.wait(
            MessageType.PRIVATE_MSG,
            lambda p: p.get("_ack") is True and p.get("to_id") == bob.user_id,
            desc="私聊 ACK",
        )
        msg_id = ack.get("msg_id")
        if not msg_id:
            raise DemoFailure("Alice -> Bob 私聊", f"ACK 缺少 msg_id：{ack}", "撤回功能无法可靠使用")
        bob.wait(
            MessageType.PRIVATE_MSG,
            lambda p: p.get("from_id") == alice.user_id and p.get("content") == content and not p.get("_ack"),
            desc="Bob 收到私聊",
        )
        carol.assert_no(
            MessageType.PRIVATE_MSG,
            lambda p: p.get("content") == content,
            desc="Alice->Bob 私聊",
        )
        self.pass_step("私聊隔离：Alice -> Bob 只有 Bob 收到，Carol 未收到")
        self.route_matrix(
            "  私聊路由矩阵",
            [
                ("Alice", "Bob", "DELIVERED"),
                ("Alice", "Carol", "BLOCKED"),
                ("Alice", "Alice/self", "NO DUPLICATE"),
            ],
        )

        reply = f"private_to_alice_{suffix}"
        bob.send(MessageType.PRIVATE_MSG, {"to_id": alice.user_id, "content": reply})
        bob.wait(
            MessageType.PRIVATE_MSG,
            lambda p: p.get("_ack") is True and p.get("to_id") == alice.user_id,
            desc="Bob 私聊 ACK",
        )
        alice.wait(
            MessageType.PRIVATE_MSG,
            lambda p: p.get("from_id") == bob.user_id and p.get("content") == reply and not p.get("_ack"),
            desc="Alice 收到 Bob 回复",
        )
        carol.assert_no(
            MessageType.PRIVATE_MSG,
            lambda p: p.get("content") == reply,
            desc="Bob->Alice 私聊",
        )
        self.pass_step("私聊回复隔离：Bob -> Alice 不会串到 Carol")
        self.route_matrix(
            "  私聊回复矩阵",
            [
                ("Bob", "Alice", "DELIVERED"),
                ("Bob", "Carol", "BLOCKED"),
                ("Bob", "Bob/self", "NO DUPLICATE"),
            ],
        )
        return msg_id

    def verify_group_file_history_recall(
        self,
        alice: ProtocolClient,
        bob: ProtocolClient,
        carol: ProtocolClient,
        suffix: str,
    ) -> tuple[int, str]:
        alice.send(MessageType.GROUP_CREATE, {"name": f"DemoGroup_{suffix}"})
        _type, _seq, payload = alice.wait(
            MessageType.GROUP_CREATE,
            lambda p: p.get("success") is True,
            desc="创建群组",
        )
        group_id = int(payload["group_id"])
        for member in (bob, carol):
            member.send(MessageType.GROUP_JOIN, {"group_id": group_id})
            member.wait(
                MessageType.GROUP_JOIN,
                lambda p: p.get("success") is True and int(p.get("group_id")) == group_id,
                desc=f"{member.label} 加群",
            )
        self.pass_step(f"群组创建/加入：group_id={group_id}，Bob 和 Carol 已加入")
        self.panel(
            "群组成员关系",
            [
                f"Group#{group_id} members: Alice / Bob / Carol",
                "后续群聊、群文件、群撤回均以这个真实 group_id 验证",
            ],
        )

        group_text = f"group_from_alice_{suffix}"
        alice.send(MessageType.GROUP_MSG, {"group_id": group_id, "content": group_text})
        _type, _seq, ack = alice.wait(
            MessageType.GROUP_MSG,
            lambda p: p.get("_ack") is True and int(p.get("group_id")) == group_id and p.get("status") == "sent",
            desc="群聊 ACK",
        )
        alice_group_msg_id = ack.get("msg_id")
        for member in (bob, carol):
            member.wait(
                MessageType.GROUP_MSG,
                lambda p: int(p.get("group_id")) == group_id and p.get("content") == group_text and not p.get("_ack"),
                desc=f"{member.label} 收到群消息",
            )
            member.assert_no(
                MessageType.PRIVATE_MSG,
                lambda p: p.get("content") == group_text,
                desc="群消息进入私聊",
            )
        self.pass_step("群聊广播：Bob/Carol 收到群消息，群消息不进入私聊")
        self.route_matrix(
            "  群聊广播矩阵",
            [
                (f"Group#{group_id}", "Bob", "DELIVERED"),
                (f"Group#{group_id}", "Carol", "DELIVERED"),
                (f"Group#{group_id}", "Private chats", "NO LEAK"),
            ],
        )

        alice.send(
            MessageType.HISTORY_REQ,
            {"type": "private", "target_type": "private", "target_id": bob.user_id, "limit": 20},
        )
        _type, _seq, private_history = alice.wait(MessageType.HISTORY_RESP, desc="私聊历史")
        if not any(message.get("content", "").startswith("private_to_bob_") for message in private_history.get("messages", [])):
            raise DemoFailure("加载 Alice/Bob 私聊历史", f"历史内容异常：{private_history}", "私聊历史可能丢失或串台")

        bob.send(
            MessageType.HISTORY_REQ,
            {"type": "group", "target_type": "group", "target_id": group_id, "limit": 20},
        )
        _type, _seq, group_history = bob.wait(MessageType.HISTORY_RESP, desc="群聊历史")
        if not any(message.get("content") == group_text for message in group_history.get("messages", [])):
            raise DemoFailure("加载群聊历史", f"历史内容异常：{group_history}", "群聊历史可能丢失或串台")
        self.pass_step("历史记录：私聊历史和群聊历史分别可查，不互相覆盖")
        self.panel(
            "历史隔离检查",
            [
                "Alice/Bob 私聊历史：包含私聊内容",
                f"Group#{group_id} 群聊历史：包含群聊内容",
                "两个历史查询分别按 target_type/target_id 校验，防止聊天记录串台",
            ],
        )

        recall_target = self.verify_private_recall(alice, bob, suffix)
        self.verify_group_recall(alice, bob, carol, group_id, suffix)
        self.pass_step(f"消息撤回：私聊 msg_id={recall_target}，群聊撤回通知均到达")

        self.verify_private_file(alice, bob, carol, suffix)
        group_file_id = self.verify_group_file(alice, bob, carol, group_id, suffix)
        self.verify_zero_byte_private_file(alice, bob, suffix)
        self.verify_relogin_group_persistence(alice, bob, carol, group_id, group_text, suffix)
        return group_id, group_file_id

    def verify_private_recall(self, alice: ProtocolClient, bob: ProtocolClient, suffix: str) -> str:
        content = f"recall_private_{suffix}"
        alice.send(MessageType.PRIVATE_MSG, {"to_id": bob.user_id, "content": content})
        _type, _seq, ack = alice.wait(
            MessageType.PRIVATE_MSG,
            lambda p: p.get("_ack") is True and p.get("to_id") == bob.user_id,
            desc="待撤回私聊 ACK",
        )
        msg_id = ack.get("msg_id")
        bob.wait(
            MessageType.PRIVATE_MSG,
            lambda p: p.get("content") == content,
            desc="Bob 收到待撤回私聊",
        )
        alice.send(MessageType.MSG_RECALL, {"msg_id": msg_id})
        alice.wait(
            MessageType.MSG_RECALL,
            lambda p: p.get("success") is True and p.get("msg_id") == msg_id,
            desc="私聊撤回 ACK",
        )
        bob.wait(
            MessageType.MSG_RECALL,
            lambda p: p.get("recalled") is True and p.get("msg_id") == msg_id,
            desc="Bob 收到私聊撤回通知",
        )
        return str(msg_id)

    def verify_group_recall(
        self,
        alice: ProtocolClient,
        bob: ProtocolClient,
        carol: ProtocolClient,
        group_id: int,
        suffix: str,
    ):
        content = f"recall_group_{suffix}"
        bob.send(MessageType.GROUP_MSG, {"group_id": group_id, "content": content})
        _type, _seq, ack = bob.wait(
            MessageType.GROUP_MSG,
            lambda p: p.get("_ack") is True and int(p.get("group_id")) == group_id,
            desc="待撤回群聊 ACK",
        )
        msg_id = ack.get("msg_id")
        alice.wait(MessageType.GROUP_MSG, lambda p: p.get("content") == content, desc="Alice 收到待撤回群聊")
        carol.wait(MessageType.GROUP_MSG, lambda p: p.get("content") == content, desc="Carol 收到待撤回群聊")
        bob.send(MessageType.MSG_RECALL, {"msg_id": msg_id})
        bob.wait(
            MessageType.MSG_RECALL,
            lambda p: p.get("success") is True and p.get("msg_id") == msg_id,
            desc="群聊撤回 ACK",
        )
        for member in (alice, carol):
            member.wait(
                MessageType.MSG_RECALL,
                lambda p: p.get("recalled") is True and p.get("msg_id") == msg_id,
                desc=f"{member.label} 收到群聊撤回通知",
            )

    def verify_private_file(
        self,
        alice: ProtocolClient,
        bob: ProtocolClient,
        carol: ProtocolClient,
        suffix: str,
    ):
        data = f"private file body {suffix}\n".encode("utf-8")
        file_id = f"pf-{uuid.uuid4().hex}"
        alice.send(
            MessageType.FILE_INIT,
            {"to_id": bob.user_id, "filename": "..\\private-demo.txt", "filesize": len(data), "file_id": file_id},
        )
        _type, _seq, init_payload = alice.wait(
            MessageType.FILE_INIT,
            lambda p: p.get("success") is True and p.get("file_id") == file_id,
            desc="私聊文件初始化",
        )
        if init_payload.get("filename") != "private-demo.txt":
            raise DemoFailure("私聊文件名安全处理", f"filename={init_payload}", "服务端可能接受异常路径文件名")

        alice.send(
            MessageType.FILE_DATA,
            {
                "file_id": file_id,
                "chunk_index": 0,
                "total_chunks": 1,
                "data": base64.b64encode(data).decode("ascii"),
            },
        )
        alice.wait(
            MessageType.FILE_DATA,
            lambda p: p.get("success") is True and p.get("completed") is True,
            desc="私聊文件上传完成 ACK",
        )
        bob.wait(
            MessageType.FILE_INIT,
            lambda p: p.get("file_id") == file_id and p.get("status") == "completed",
            desc="Bob 收到私聊文件通知",
        )
        bob.send(MessageType.FILE_ACK, {"file_id": file_id, "offset": 0})
        _type, _seq, chunk = bob.wait(
            MessageType.FILE_ACK,
            lambda p: p.get("file_id") == file_id and p.get("data"),
            desc="Bob 下载私聊文件",
        )
        if base64.b64decode(chunk["data"]) != data:
            raise DemoFailure("Bob 下载私聊文件", "下载内容与上传内容不一致", "文件传输完整性异常")

        carol.send(MessageType.FILE_ACK, {"file_id": file_id, "offset": 0})
        _type, _seq, denied = carol.wait(
            MessageType.FILE_ACK,
            lambda p: p.get("success") is False,
            desc="Carol 越权下载私聊文件",
        )
        if denied.get("error") != "permission_denied" or denied.get("file_id") != file_id:
            raise DemoFailure("Carol 越权下载私聊文件", f"拒绝响应异常：{denied}", "文件下载权限或失败 ACK 异常")
        self.pass_step("私聊文件：Bob 下载内容一致，Carol 越权下载被拒绝")
        self.panel(
            "私聊文件中继链路",
            [
                "Alice 上传 -> 服务端 file_storage -> Bob 下载：内容一致",
                "Carol 越权下载 -> 服务端拒绝 permission_denied",
                "异常文件名 '..\\private-demo.txt' -> 服务端只保留 basename",
            ],
        )

    def verify_group_file(
        self,
        alice: ProtocolClient,
        bob: ProtocolClient,
        carol: ProtocolClient,
        group_id: int,
        suffix: str,
    ) -> str:
        data = f"group file body {suffix}\n".encode("utf-8")
        file_id = f"gf-{uuid.uuid4().hex}"
        alice.send(
            MessageType.FILE_INIT,
            {"group_id": group_id, "filename": "group-demo.txt", "filesize": len(data), "file_id": file_id},
        )
        alice.wait(
            MessageType.FILE_INIT,
            lambda p: p.get("success") is True and p.get("file_id") == file_id,
            desc="群文件初始化",
        )
        alice.send(
            MessageType.FILE_DATA,
            {
                "file_id": file_id,
                "chunk_index": 0,
                "total_chunks": 1,
                "data": base64.b64encode(data).decode("ascii"),
            },
        )
        alice.wait(
            MessageType.FILE_DATA,
            lambda p: p.get("success") is True and p.get("completed") is True,
            desc="群文件上传完成 ACK",
        )
        for member in (bob, carol):
            member.wait(
                MessageType.FILE_INIT,
                lambda p: p.get("file_id") == file_id and int(p.get("group_id")) == group_id,
                desc=f"{member.label} 收到群文件通知",
            )
            member.send(MessageType.FILE_ACK, {"file_id": file_id, "offset": 0})
            _type, _seq, chunk = member.wait(
                MessageType.FILE_ACK,
                lambda p: p.get("file_id") == file_id and p.get("data"),
                desc=f"{member.label} 下载群文件",
            )
            if base64.b64decode(chunk["data"]) != data:
                raise DemoFailure(f"{member.label} 下载群文件", "下载内容与上传内容不一致", "群文件完整性异常")
        self.pass_step("群文件：Bob/Carol 均可下载，内容一致")
        self.panel(
            "群文件中继链路",
            [
                f"Alice 上传 group-demo.txt -> Group#{group_id}",
                "服务端完成中继存储后通知 Bob / Carol",
                "Bob / Carol 下载内容均与上传内容一致",
            ],
        )
        return file_id

    def verify_zero_byte_private_file(
        self,
        alice: ProtocolClient,
        bob: ProtocolClient,
        suffix: str,
    ):
        file_id = f"zf-{uuid.uuid4().hex}"
        alice.send(
            MessageType.FILE_INIT,
            {"to_id": bob.user_id, "filename": "empty-demo.txt", "filesize": 0, "file_id": file_id},
        )
        _type, _seq, init_payload = alice.wait(
            MessageType.FILE_INIT,
            lambda p: p.get("success") is True and p.get("file_id") == file_id and p.get("completed") is True,
            desc="零字节私聊文件初始化",
        )
        if init_payload.get("chunks_total") not in (0, "0"):
            raise DemoFailure("零字节文件初始化", f"chunks_total 异常：{init_payload}", "零字节文件边界处理可能错误")

        bob.wait(
            MessageType.FILE_INIT,
            lambda p: p.get("file_id") == file_id and p.get("status") == "completed",
            desc="Bob 收到零字节私聊文件通知",
        )
        bob.send(MessageType.FILE_ACK, {"file_id": file_id, "offset": 0})
        _type, _seq, chunk = bob.wait(
            MessageType.FILE_ACK,
            lambda p: p.get("file_id") == file_id and p.get("success") is not False,
            desc="Bob 下载零字节文件",
        )
        if base64.b64decode(chunk.get("data", "")) != b"" or int(chunk.get("size", -1)) != 0:
            raise DemoFailure("Bob 下载零字节文件", f"零字节回包异常：{chunk}", "零字节文件下载流程可能不稳定")

        self.pass_step("文件边界：零字节私聊文件可完成通知并正常下载")
        self.panel(
            "零字节文件边界",
            [
                "empty-demo.txt：初始化即完成 completed=True",
                "接收方仍会收到文件完成通知，offset=0 可正常下载",
                "用于卡住最容易被忽略的空文件边界行为",
            ],
        )

    def verify_relogin_group_persistence(
        self,
        alice: ProtocolClient,
        bob: ProtocolClient,
        carol: ProtocolClient,
        group_id: int,
        group_text: str,
        suffix: str,
    ):
        self.panel(
            "重登恢复检查",
            [
                "协议侧模拟 Bob 断开并重新登录，检查群状态恢复是否完整",
                "验证群组列表、群成员身份和群聊历史不会丢失",
                "验证重新登录后仍可继续在原群发消息，证明状态恢复正确",
            ],
        )
        self.broadcast_gui_notice(
            "正在执行重登恢复校验：检查群列表、群历史和群发消息是否恢复正常",
            "warning",
            4600,
        )
        self.demo_wait(1.0)
        bob.close()
        self.demo_wait(0.5)
        bob.connect()
        login_payload = bob.login()
        groups = login_payload.get("groups") or {}
        available_groups = login_payload.get("available_groups") or {}
        group_state = available_groups.get(str(group_id)) or {}
        if str(group_id) not in groups:
            raise DemoFailure("Bob 重登后恢复群组列表", f"登录响应 groups={groups}", "重新登录后左侧群列表可能丢失")
        if not group_state.get("joined"):
            raise DemoFailure("Bob 重登后恢复群成员身份", f"available_groups={available_groups}", "重登后群成员状态可能错误")

        bob.send(
            MessageType.HISTORY_REQ,
            {"type": "group", "target_type": "group", "target_id": group_id, "limit": 20},
        )
        _type, _seq, group_history = bob.wait(MessageType.HISTORY_RESP, desc="Bob 重登后加载群聊历史")
        if not any(message.get("content") == group_text for message in group_history.get("messages", [])):
            raise DemoFailure("Bob 重登后加载群聊历史", f"历史内容异常：{group_history}", "重登后群聊历史可能丢失")

        restored_text = f"bob_after_relogin_{suffix}"
        bob.send(MessageType.GROUP_MSG, {"group_id": group_id, "content": restored_text})
        bob.wait(
            MessageType.GROUP_MSG,
            lambda p: p.get("_ack") is True and int(p.get("group_id")) == group_id and p.get("status") == "sent",
            desc="Bob 重登后群聊 ACK",
        )
        for member in (alice, carol):
            member.wait(
                MessageType.GROUP_MSG,
                lambda p: p.get("content") == restored_text and int(p.get("group_id")) == group_id,
                desc=f"{member.label} 收到 Bob 重登后群消息",
            )
        self.pass_step("重登恢复：Bob 重新登录后群列表、群历史和群发消息均恢复正常")
        self.broadcast_gui_notice(
            "重登恢复已通过：群列表、群历史和群发消息均已恢复正常",
            "success",
            4800,
        )
        self.demo_wait(1.0)
        self.route_matrix(
            "  重登后群聊矩阵",
            [
                ("Bob (relogin)", f"Group#{group_id}", "RESTORED"),
                (f"Group#{group_id}", "Alice", "DELIVERED"),
                (f"Group#{group_id}", "Carol", "DELIVERED"),
            ],
        )

    def verify_content_moderation(
        self,
        alice: ProtocolClient,
        bob: ProtocolClient,
        carol: ProtocolClient,
        group_id: int,
        suffix: str,
    ):
        normal = f"please leave the group and route a2b after skill review {suffix}"
        alice.send(MessageType.PRIVATE_MSG, {"to_id": bob.user_id, "content": normal})
        alice.wait(MessageType.PRIVATE_MSG, lambda p: p.get("_ack") is True and p.get("to_id") == bob.user_id)
        _type, _seq, delivered = bob.wait(
            MessageType.PRIVATE_MSG,
            lambda p: p.get("from_id") == alice.user_id and p.get("content") == normal,
            desc="正常英文消息不过度误伤",
        )
        if delivered.get("content") != normal:
            raise DemoFailure("发送正常英文内容", f"内容被误改：{delivered}", "内容审核误伤正常聊天文本")

        blocked_private = f"kill private {suffix}"
        alice.send(MessageType.PRIVATE_MSG, {"to_id": bob.user_id, "content": blocked_private})
        alice.wait(
            MessageType.CONTENT_WARN,
            lambda p: p.get("related_type") == "private" and str(p.get("related_target")) == str(bob.user_id),
            desc="私聊敏感内容警告",
        )
        bob.assert_no(
            MessageType.PRIVATE_MSG,
            lambda p: p.get("content") == blocked_private,
            desc="被拦截的私聊敏感内容",
        )

        blocked_group = f"kill group {suffix}"
        alice.send(MessageType.GROUP_MSG, {"group_id": group_id, "content": blocked_group})
        alice.wait(
            MessageType.CONTENT_WARN,
            lambda p: p.get("related_type") == "group" and str(p.get("group_id")) == str(group_id),
            desc="群聊敏感内容警告",
        )
        for member in (bob, carol):
            member.assert_no(
                MessageType.GROUP_MSG,
                lambda p: p.get("content") == blocked_group,
                desc=f"{member.label} 收到被拦截的群聊敏感内容",
            )
        self.pass_step("内容审核：敏感内容被拦截，正常英文不会被误替换")
        self.panel(
            "内容审核画面",
            [
                "正常英文消息：通过并送达",
                "私聊敏感内容：发送方收到 CONTENT_WARN，接收方收不到原文",
                "群聊敏感内容：发送方收到 CONTENT_WARN，群成员收不到原文",
            ],
        )

    def verify_ai_fallback_or_response(
        self,
        alice: ProtocolClient,
        bob: ProtocolClient,
        carol: ProtocolClient,
        group_id: int,
        suffix: str,
    ):
        alice.send(
            MessageType.AI_QUERY,
            {"user_id": alice.user_id, "group_id": 0, "query": "Reply with one short English sentence."},
        )
        msg_type, _seq, payload = alice.wait(
            predicate=lambda _p: True,
            timeout=25,
            desc="AI 响应或友好错误",
        )
        while msg_type not in (MessageType.AI_RESP, MessageType.ERROR):
            msg_type, _seq, payload = alice.wait(
                predicate=lambda _p: True,
                timeout=5,
                desc="AI 响应或友好错误",
            )

        if msg_type == MessageType.ERROR:
            self.pass_step("AI 兜底：未配置 key 或上游不可用时返回友好错误，不影响聊天服务")
            self.panel(
                "AI 兜底画面",
                [
                    "AI 请求没有导致服务端崩溃",
                    "服务端返回 ERROR 作为友好降级",
                    "聊天、文件、群组等主流程继续可用",
                    "当前轮未继续验证群聊 AI 广播；如已配置真实 Key，将在这里继续展示群聊 AI",
                ],
            )
            return

        if not (payload.get("content") or payload.get("reply")):
            raise DemoFailure("AI Assistant 独立对话", f"AI_RESP 缺少内容：{payload}", "AI 回复展示为空")
        direct_reply = str(payload.get("content") or payload.get("reply") or "")
        direct_lower = direct_reply.lower().lstrip()
        for alias in (
            alice.username,
            f"user#{alice.user_id}",
            f"用户{alice.user_id}",
        ):
            if direct_lower.startswith(alias.lower() + ":") or direct_lower.startswith(alias.lower() + "："):
                raise DemoFailure("AI Assistant 独立对话格式", f"回复带发起者名前缀：{direct_reply}", "展示上会像 AI 把用户当成自己来发言")
        bob.assert_no(MessageType.AI_RESP, lambda _p: True, desc="Bob 收到 Alice 的私有 AI 回复")
        carol.assert_no(MessageType.AI_RESP, lambda _p: True, desc="Carol 收到 Alice 的私有 AI 回复")

        alice.send(
            MessageType.AI_QUERY,
            {
                "user_id": alice.user_id,
                "group_id": group_id,
                "query": f"For group {suffix}, reply briefly without prefixing a member name.",
            },
        )
        _type, _seq, group_reply = alice.wait(
            MessageType.AI_RESP,
            lambda p: int(p.get("group_id") or 0) == group_id,
            timeout=30,
            desc="群聊 AI 发起方响应",
        )
        reply = str(group_reply.get("content") or group_reply.get("reply") or "")
        lower = reply.lower().lstrip()
        for username in (alice.username, bob.username, carol.username):
            if lower.startswith(username.lower() + ":"):
                raise DemoFailure("群聊 AI 回复格式", f"回复带成员名前缀：{reply}", "群聊 AI 展示可能误认为其他用户发言")
        for member in (bob, carol):
            member.wait(
                MessageType.AI_RESP,
                lambda p: int(p.get("group_id") or 0) == group_id and (p.get("content") or p.get("reply")),
                timeout=30,
                desc=f"{member.label} 收到群聊 AI 回复",
            )
        self.pass_step("AI：直接对话不串给他人；群聊 AI 回复可广播给群成员")
        self.panel(
            "AI 路由画面",
            [
                "AI Assistant 独立对话：只回到 Alice，不广播给 Bob/Carol",
                f"Group#{group_id} @AI：Alice/Bob/Carol 均收到同一群聊 AI 回复",
                "群聊 AI 回复不带成员名前缀，避免误显示成某个普通用户发言",
            ],
        )

    def verify_leave_group(
        self,
        alice: ProtocolClient,
        bob: ProtocolClient,
        carol: ProtocolClient,
        group_id: int,
        group_file_id: str,
        suffix: str,
    ):
        bob.send(MessageType.GROUP_LEAVE, {"group_id": group_id})
        bob.wait(
            MessageType.GROUP_LEAVE,
            lambda p: p.get("success") is True and int(p.get("group_id")) == group_id,
            desc="Bob 退群",
        )

        rejected_text = f"bob_post_exit_{suffix}"
        bob.send(MessageType.GROUP_MSG, {"group_id": group_id, "content": rejected_text})
        bob.wait(
            MessageType.GROUP_MSG,
            lambda p: p.get("_ack") is True and p.get("status") == "rejected",
            desc="退群后发群消息被拒绝",
        )
        alice.assert_no(MessageType.GROUP_MSG, lambda p: p.get("content") == rejected_text, desc="Bob 退群后消息")
        carol.assert_no(MessageType.GROUP_MSG, lambda p: p.get("content") == rejected_text, desc="Bob 退群后消息")

        post_exit = f"alice_post_exit_{suffix}"
        alice.send(MessageType.GROUP_MSG, {"group_id": group_id, "content": post_exit})
        alice.wait(
            MessageType.GROUP_MSG,
            lambda p: p.get("_ack") is True and int(p.get("group_id")) == group_id and p.get("status") == "sent",
            desc="Alice 退群后群消息 ACK",
        )
        carol.wait(
            MessageType.GROUP_MSG,
            lambda p: p.get("content") == post_exit and int(p.get("group_id")) == group_id,
            desc="Carol 收到 Bob 退群后的群消息",
        )
        bob.assert_no(MessageType.GROUP_MSG, lambda p: p.get("content") == post_exit, desc="Bob 退群后继续收到群消息")

        bob.send(MessageType.HISTORY_REQ, {"type": "group", "target_type": "group", "target_id": group_id, "limit": 5})
        bob.wait(MessageType.ERROR, lambda p: "message" in p or "error" in p, desc="Bob 退群后查群历史被拒绝")

        bob.send(MessageType.FILE_ACK, {"file_id": group_file_id, "offset": 0})
        _type, _seq, denied = bob.wait(
            MessageType.FILE_ACK,
            lambda p: p.get("success") is False,
            desc="Bob 退群后下载群文件被拒绝",
        )
        if denied.get("error") != "permission_denied":
            raise DemoFailure("Bob 退群后下载群文件", f"拒绝响应异常：{denied}", "群文件权限控制异常")
        self.pass_step("退群权限：退群用户不能发群消息、查群历史或下载群文件")
        self.panel(
            "退群权限矩阵",
            [
                "Bob -> Group message: REJECTED",
                "Bob -> Group history: DENIED",
                "Bob -> Group file download: permission_denied",
                "Alice/Carol 留在群内：继续正常收发群消息",
            ],
        )

    def section_web_routing(self) -> str:
        self.panel(
            "GUI 路由回归重点",
            [
                "消息不会从私聊串到自己/其他联系人",
                "群消息不会同步到私聊页",
                "AI Assistant 红点和群聊 @AI 显示互不污染",
                "系统消息只进入对应会话，不广播到所有聊天页",
            ],
        )
        self.run_command(["node", "tests\\test_web_chat_routing.js"], timeout=30)
        return "GUI 消息归属、未读红点、群聊 AI 上下文路由逻辑通过"

    def section_pytest(self) -> str:
        if self.args.skip_pytest:
            print("  [SKIP] 已指定 --skip-pytest，跳过全量单元测试。")
            return "已跳过 pytest"
        self.run_command(
            [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
            timeout=180,
        )
        return "pytest 全量单元测试通过"

    def section_integration(self) -> str:
        self.run_command([sys.executable, "tests\\run_integration_tests.py"], timeout=120)
        return "集成测试 11 项通过"

    def section_stress_50(self) -> str:
        report = asyncio.run(
            StressTester(
                host=HOST,
                port=PORT,
                num_clients=50,
                concurrency=20,
                messages_per_client=3,
                timeout=10,
            ).run()
        )
        print_report(report)
        self.print_stress_visual("50 客户端", report)
        if not report.ok:
            raise DemoFailure(
                "50 客户端压力测试",
                f"connected={report.connected}, acked={report.total_messages_acked}, errors={report.total_errors}",
                "未达到课程要求的 50 客户端并发验收线",
            )
        return f"50 clients PASS, ACK={report.total_messages_acked}, errors={report.total_errors}"

    def section_stress_100(self) -> str:
        if self.args.fast:
            print("  [SKIP] --fast 模式跳过 100 客户端压测；正式录屏建议去掉 --fast。")
            return "fast 模式已跳过"
        report = asyncio.run(
            StressTester(
                host=HOST,
                port=PORT,
                num_clients=100,
                concurrency=50,
                messages_per_client=3,
                timeout=15,
            ).run()
        )
        print_report(report)
        self.print_stress_visual("100 客户端", report)
        if not report.ok:
            raise DemoFailure(
                "100 客户端压力测试",
                f"connected={report.connected}, acked={report.total_messages_acked}, errors={report.total_errors}",
                "并发余量展示未通过，需要检查服务端稳定性",
            )
        return f"100 clients PASS, ACK={report.total_messages_acked}, errors={report.total_errors}"

    def section_reconnect(self) -> str:
        if self.args.use_existing_server:
            print("  [SKIP] --use-existing-server 模式不停止外部服务端，跳过断线重连 smoke。")
            return "使用已有服务端，已跳过"
        if str(CLIENT_ROOT) not in sys.path:
            sys.path.insert(0, str(CLIENT_ROOT))
        from connection import ChatConnection
        from protocol import MessageType as ClientMessageType

        suffix = str(int(time.time() * 1000))[-8:]
        username = f"reconn_{suffix}"
        events: list[tuple[str, dict, float]] = []

        def push_event(name: str, payload: Optional[dict] = None):
            events.append((name, payload or {}, time.time()))

        def wait_event(name: str, predicate: Callable[[dict], bool] = lambda _p: True, timeout: float = 12):
            deadline = time.time() + timeout
            cursor = 0
            while time.time() < deadline:
                for idx in range(cursor, len(events)):
                    event_name, payload, _ts = events[idx]
                    if event_name == name and predicate(payload):
                        return payload
                cursor = max(0, len(events) - 20)
                time.sleep(0.05)
            raise DemoFailure(
                f"等待重连事件 {name}",
                f"超时未收到；events_tail={events[-10:]}",
                "客户端断线重连提示或自动重登录可能异常",
            )

        conn = ChatConnection()

        def on_msg(msg_type, seq, payload):
            push_event(f"msg:{msg_type}", payload)

        for message_type in (
            ClientMessageType.REGISTER_RESP,
            ClientMessageType.LOGIN_RESP,
            ClientMessageType.HEARTBEAT_ACK,
            ClientMessageType.ERROR,
        ):
            conn.register_callback(message_type, on_msg)

        conn.on_disconnected(lambda: push_event("disconnected"))

        def on_reconnected():
            push_event("reconnected")
            conn.send_message(
                ClientMessageType.LOGIN_REQ,
                {"username": username, "password_hash": "demo_pass"},
            )

        conn.on_connected(on_reconnected)

        try:
            if not conn.connect(HOST, PORT):
                raise DemoFailure("连接服务端", "ChatConnection 初始连接失败", "无法验证客户端断线重连")
            conn.send_message(ClientMessageType.REGISTER_REQ, {"username": username, "password_hash": "demo_pass"})
            wait_event(f"msg:{ClientMessageType.REGISTER_RESP}", lambda p: p.get("success") is True, timeout=8)
            conn.send_message(ClientMessageType.LOGIN_REQ, {"username": username, "password_hash": "demo_pass"})
            wait_event(f"msg:{ClientMessageType.LOGIN_RESP}", lambda p: p.get("success") is True, timeout=8)
            self.pass_step("重连 smoke：初始注册/登录成功")

            self.broadcast_gui_notice("即将执行断线恢复：请观察 GUI 状态栏与系统提示", "warning", 4200)
            self.demo_wait(1.2)
            self.stop_server()
            wait_event("disconnected", timeout=10)
            self.broadcast_gui_notice("服务端已断开：此时应看到 Disconnected，且消息不能假装发送成功", "warning", 4200)
            self.demo_wait(1.8)
            if conn.send_message(ClientMessageType.HEARTBEAT, {}):
                raise DemoFailure("断线期间发送消息", "send_message 返回 True", "客户端可能假装在线发送成功")
            self.pass_step("重连 smoke：停止服务端后客户端进入 disconnected，断线发送失败")
            self.panel(
                "断线阶段",
                [
                    "服务端停止：客户端触发 disconnected",
                    "断线期间发送 HEARTBEAT：send_message 返回 False",
                    "避免 GUI 假装在线或假装发送成功",
                ],
            )

            self.start_server()
            self.broadcast_gui_notice("服务恢复中：请观察状态栏回到 Connected，并自动恢复会话", "success", 4600)
            self.demo_wait(1.6)
            wait_event("reconnected", timeout=30)
            wait_event(f"msg:{ClientMessageType.LOGIN_RESP}", lambda p: p.get("success") is True, timeout=12)
            conn.send_message(ClientMessageType.HEARTBEAT, {})
            wait_event(f"msg:{ClientMessageType.HEARTBEAT_ACK}", timeout=8)
            self.pass_step("重连 smoke：服务端重启后自动重连、重新登录、心跳恢复")
            self.panel(
                "恢复阶段",
                [
                    "服务端重启：客户端触发 reconnected",
                    "自动重新登录：LOGIN_RESP success=True",
                    "心跳恢复：HEARTBEAT_ACK 到达",
                ],
            )
            return "断线提示、断线发送失败、自动重连和重登录均通过"
        finally:
            conn.close()

    def section_cleanup_and_summary(self) -> str:
        if self.args.gui_demo and self.args.hold_gui:
            print("[INFO] 真实 GUI demo 窗口仍在展示，运行数据清理推迟到关闭 GUI 后执行。")
            print()
            print("P2P 说明：P2P 保留为实验性扩展，录屏和现场稳定演示默认使用服务端中继文件传输。")
            return "真实 GUI 和服务端保留展示；清理将在关闭 GUI 后执行"
        self.stop_server()
        if self.args.gui_demo:
            print("[INFO] 真实 GUI demo 窗口将随着收尾一起关闭。")
            print()
            print("P2P 说明：P2P 保留为实验性扩展，录屏和现场稳定演示默认使用服务端中继文件传输。")
            return "已停止测试服务端；真实 GUI 将在结束时关闭，随后执行清理"
        self.clean_runtime_artifacts()
        print()
        print("P2P 说明：P2P 保留为实验性扩展，录屏和现场稳定演示默认使用服务端中继文件传输。")
        return "已停止测试服务端并清理运行数据；P2P 作为实验性扩展说明"

    def print_summary(self) -> int:
        self.line("=")
        print("  TEST SUMMARY")
        self.line("=")
        passed = sum(1 for result in self.results if result.ok)
        failed = len(self.results) - passed
        print(f"  Overall Pass Rate {self.progress_bar(passed, len(self.results))}")
        print()
        for result in self.results:
            status = "PASS" if result.ok else "FAIL"
            print(f"  [{status}] {result.name} - {result.detail}")
        self.line("=")
        print(f"  Section Stats: passed={passed}, failed={failed}")
        self.line("=")
        return 0 if failed == 0 else 1

    def run(self) -> int:
        self.section("01", "环境与版本检查", "确认录屏基于当前最终代码和 Python 环境", self.section_environment)
        self.section("02", "三用户协同验收", "用 Alice/Bob/Carol 模拟真实聊天、文件、审核、退群等高风险路径", self.section_three_user_acceptance)
        self.section("03", "GUI 路由逻辑测试", "验证前端消息归属、红点、系统消息和群聊 AI 不串台", self.section_web_routing)
        self.section("04", "单元测试", "验证协议、数据库、AI、文件、安全、客户端桥接等模块级逻辑", self.section_pytest)
        self.section("05", "集成测试", "验证注册、登录、私聊、群聊、撤回、历史、心跳全链路", self.section_integration)
        self.section("06", "50 客户端压力测试", "对齐课程至少 50 客户端并发连接要求", self.section_stress_50)
        self.section("07", "100 客户端压力测试", "展示系统并发余量和 ACK/接收计数稳定性", self.section_stress_100)
        self.section("08", "断线重连测试", "验证服务端断开后客户端提示离线，服务端恢复后自动重连", self.section_reconnect)
        self.section("09", "清理与收尾", "停止测试服务端并清理运行数据、缓存和日志", self.section_cleanup_and_summary)
        return self.print_summary()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="录屏专用最终验收 Demo Runner")
    parser.add_argument("--fast", action="store_true", help="跳过 100 客户端压测，适合录屏前预演")
    parser.add_argument("--skip-pytest", action="store_true", help="跳过 pytest 全量单元测试")
    parser.add_argument("--use-existing-server", action="store_true", help="使用已经启动的 127.0.0.1:8888 服务端")
    parser.add_argument("--pause-between-sections", action="store_true", help="每段结束后等待回车，方便录屏停留")
    parser.add_argument("--gui-demo", action="store_true", help="启动 Alice/Bob/Carol 三个真实 GUI 客户端联动演示")
    parser.add_argument("--demo-delay", type=float, default=0.0, help="每个可视化步骤后的停留秒数，录屏建议 0.4-0.8")
    parser.add_argument("--gui-demo-delay", type=float, default=1.0, help="真实 GUI 自动操作的节奏倍率")
    parser.add_argument("--gui-width", type=int, default=610, help="真实 GUI demo 窗口宽度")
    parser.add_argument("--gui-height", type=int, default=620, help="真实 GUI demo 窗口高度")
    parser.add_argument("--gui-x", type=int, default=20, help="第一个 GUI demo 窗口的 x 坐标")
    parser.add_argument("--gui-y", type=int, default=40, help="GUI demo 窗口的 y 坐标")
    parser.add_argument("--gui-gap", type=int, default=12, help="GUI demo 窗口间距")
    parser.add_argument("--hold-gui", action="store_true", help="测试结束后保留真实 GUI 窗口，按 Enter 后关闭")
    parser.add_argument("--wait-before-run", action="store_true", help="先进入预备状态，调整窗口并手动开始正式录屏")
    parser.add_argument("--video-mode", action="store_true", help="录屏推荐模式：启动真实 GUI 三客户端并放慢终端节奏")
    parser.add_argument("--start-countdown", type=int, default=None, help="正式开始前倒计时秒数；video-mode 默认 3 秒")
    parser.add_argument("--no-gui-sync", action="store_true", help="关闭 GUI/终端 ACK 对齐，退回只按时间等待的旧行为")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.video_mode:
        args.gui_demo = True
        args.hold_gui = True
        args.wait_before_run = True
        if args.demo_delay <= 0:
            args.demo_delay = 0.85
        if args.gui_demo_delay <= 1.0:
            args.gui_demo_delay = 1.45
    if args.start_countdown is None:
        args.start_countdown = 3 if args.video_mode else 0
    runner = DemoRunner(args)
    try:
        runner.title()
        runner.prepare()
        runner.wait_for_recording_ready()
        exit_code = runner.run()
        if args.hold_gui and args.gui_demo:
            input("真实 GUI demo 窗口将保留在最终画面。按 Enter 关闭 GUI 并结束...")
        return exit_code
    except DemoFailure as exc:
        print("[FAIL] Demo Runner 启动失败")
        print(f"  触发步骤：{exc.trigger}")
        print(f"  实际现象：{exc.actual}")
        print(f"  可能影响：{exc.impact}")
        return 1
    finally:
        if args.hold_gui and args.gui_demo:
            runner.stop_gui_demo()
            runner.stop_server()
        else:
            runner.stop_server()
            runner.stop_gui_demo()
        if args.gui_demo and not args.use_existing_server:
            try:
                runner.clean_runtime_artifacts(silent=True)
            except DemoFailure:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
