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
    args = parser.parse_args()

    if args.gui:
        _start_gui(args.host, args.port)
    else:
        _start_cli(args.host, args.port)


def _start_cli(host, port):
    from cli import ChatCLI
    cli = ChatCLI(host=host, port=port)
    cli.run()


def _start_gui(host, port):
    from gui import ChatGUI
    gui = ChatGUI(host=host, port=port)
    gui.run()


if __name__ == "__main__":
    main()
