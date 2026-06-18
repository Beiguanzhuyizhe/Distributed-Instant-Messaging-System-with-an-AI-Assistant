"""
客户端入口 — 支持 CLI 和 GUI 双模启动

用法：
    python -m client.main                  # CLI 模式（默认）
    python -m client.main --gui            # GUI 模式
    python -m client.main --host 0.0.0.0 --port 8888
"""

import argparse
import sys
import os


def _ensure_path():
    """确保 client/ 目录在 sys.path 中"""
    client_dir = os.path.dirname(os.path.abspath(__file__))
    if client_dir not in sys.path:
        sys.path.insert(0, client_dir)


def main():
    _ensure_path()

    parser = argparse.ArgumentParser(
        description="Chat System Client - Distributed Instant Messaging"
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="Server host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8888,
                        help="Server port (default: 8888)")
    parser.add_argument("--cli", action="store_true", default=True,
                        help="Launch CLI mode (default)")
    parser.add_argument("--gui", action="store_true",
                        help="Launch GUI mode")
    parser.add_argument("--demo-role", choices=["alice", "bob", "carol"],
                        help="Recording-only GUI demo role")
    parser.add_argument("--demo-user",
                        help="Recording-only GUI demo username")
    parser.add_argument("--demo-suffix",
                        help="Recording-only shared suffix for demo users/groups")
    parser.add_argument("--demo-password", default="demo_pass",
                        help="Recording-only demo password")
    parser.add_argument("--demo-x", type=int,
                        help="Recording-only GUI window x position")
    parser.add_argument("--demo-y", type=int,
                        help="Recording-only GUI window y position")
    parser.add_argument("--demo-width", type=int,
                        help="Recording-only GUI window width")
    parser.add_argument("--demo-height", type=int,
                        help="Recording-only GUI window height")
    parser.add_argument("--demo-delay", type=float, default=1.0,
                        help="Recording-only delay multiplier")
    parser.add_argument("--demo-start-signal",
                        help="Recording-only: wait until this signal file exists before auto-running GUI demo")
    parser.add_argument("--demo-control-file",
                        help="Recording-only: shared demo control file for synced GUI notices")
    parser.add_argument("--demo-ack-dir",
                        help="Recording-only: directory for GUI/terminal sync markers")
    args = parser.parse_args()

    if args.gui:
        _start_gui(args)
    else:
        _start_cli(args.host, args.port)


def _start_cli(host, port):
    from cli import ChatCLI
    cli = ChatCLI(host=host, port=port)
    cli.run()


def _start_gui(args):
    from gui import ChatGUI
    gui = ChatGUI(
        host=args.host,
        port=args.port,
        demo_role=args.demo_role,
        demo_user=args.demo_user,
        demo_suffix=args.demo_suffix,
        demo_password=args.demo_password,
        demo_x=args.demo_x,
        demo_y=args.demo_y,
        demo_width=args.demo_width,
        demo_height=args.demo_height,
        demo_delay=args.demo_delay,
        demo_start_signal=args.demo_start_signal,
        demo_control_file=args.demo_control_file,
        demo_ack_dir=args.demo_ack_dir,
    )
    gui.run()


if __name__ == "__main__":
    main()
