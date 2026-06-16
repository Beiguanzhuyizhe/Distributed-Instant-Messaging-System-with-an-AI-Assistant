"""
分布式即时聊天系统 - 服务端入口
"""
import asyncio
import logging
import sys

from server.config import ServerConfig
from server.tcp_server import ChatServer


def setup_logging(config: ServerConfig):
    level = getattr(logging, config.log_level.upper(), logging.INFO)
    kwargs = {
        "level": level,
        "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        "datefmt": "%Y-%m-%d %H:%M:%S",
        "stream": sys.stdout,
    }
    if config.log_file:
        kwargs["filename"] = config.log_file
        kwargs["filemode"] = "a"
    logging.basicConfig(**kwargs)


async def main():
    config = ServerConfig()
    setup_logging(config)
    server = ChatServer(config)
    try:
        await server.start()
    except KeyboardInterrupt:
        print("\nServer shutting down...")
    finally:
        await server.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
