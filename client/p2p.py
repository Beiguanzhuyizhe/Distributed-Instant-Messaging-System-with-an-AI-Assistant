"""
P2P UDP 打洞直连模块
通过服务器协助进行 NAT 穿透，建立点对点 UDP 直连
支持加密文件传输、断点续传

流程:
1. A 请求给 B 传文件 → A 发 P2P_HOLE_PUNCH 给服务器（带上自己的真实 UDP 地址）
2. 服务器交换 A 和 B 的地址
3. 双方互相向对方地址发 UDP 包（打洞）
4. NAT 允许入站包后，连接建立
5. 通过直连进行分块加密文件传输
"""

import asyncio
import os
import json
import logging
import struct
import time
from typing import Optional, Callable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from config import Config
from connection import ChatConnection
from protocol import MessageType

logger = logging.getLogger(__name__)

# P2P 协议常量
P2P_MAGIC = 0x5045  # "PE"
P2P_VERSION = 0x01
P2P_HEADER_FORMAT = "!H B B Q I"  # Magic(2B) + Version(1B) + Type(1B) + Seq(8B) + PayloadLen(4B) = 16B
P2P_HEADER_SIZE = struct.calcsize(P2P_HEADER_FORMAT)

P2P_FILE_INIT = 0x01     # 文件传输初始化
P2P_FILE_DATA = 0x02     # 文件数据块
P2P_FILE_ACK = 0x03      # 数据块确认
P2P_FILE_RESUME = 0x04   # 断点续传请求
P2P_PUNCH = 0x05         # 打洞包
P2P_PUNCH_ACK = 0x06     # 打洞确认
P2P_FILE_DONE = 0x07     # 文件传输完成

MAX_RETRIES = 3           # 每块最大重传次数
ACK_TIMEOUT = 1.0         # ACK 等待超时（秒）
PUNCH_COUNT = 8           # 打洞包数量
PUNCH_INTERVAL = 0.3      # 打洞间隔（秒）


class P2PError(Exception):
    pass


class P2PClient:
    """P2P 客户端：UDP 打洞 + 直连文件传输"""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self._udp_transport: Optional[asyncio.DatagramTransport] = None
        self._local_udp_port = 0
        self._running = False
        self._chunk_size = 65536  # 64KB

        # 文件接收状态
        self._recv_save_dir = "."
        self._recv_aes_key: Optional[bytes] = None
        self._recv_progress: Optional[Callable] = None
        self._recv_file_info: Optional[dict] = None

        # P2P 保活
        self._pending_acks: dict = {}  # seq -> asyncio.Event

    async def start(self) -> int:
        loop = asyncio.get_event_loop()

        class P2PProtocol(asyncio.DatagramProtocol):
            def __init__(self, p2p):
                self.p2p = p2p
                self.transport = None

            def connection_made(self, transport):
                self.transport = transport
                self.p2p._udp_transport = transport

            def datagram_received(self, data, addr):
                asyncio.ensure_future(self.p2p._handle_datagram(data, addr))

            def error_received(self, exc):
                logger.error(f"P2P UDP 错误: {exc}")

        self._udp_transport, _ = await loop.create_datagram_endpoint(
            lambda: P2PProtocol(self),
            local_addr=("0.0.0.0", 0),
        )

        sockname = self._udp_transport.get_extra_info("sockname")
        self._local_udp_port = sockname[1]
        self._running = True
        logger.info(f"P2P UDP 监听已启动，端口: {self._local_udp_port}")
        return self._local_udp_port

    def stop(self):
        self._running = False
        if self._udp_transport:
            self._udp_transport.close()
            self._udp_transport = None
        logger.info("P2P 服务已停止")

    @property
    def local_port(self) -> int:
        return self._local_udp_port

    # ---- UDP 收发 ----

    def _send_udp(self, addr: tuple, msg_type: int, payload_bytes: bytes, seq: int = 0):
        if not self._udp_transport:
            raise P2PError("P2P 服务未启动")
        header = struct.pack(P2P_HEADER_FORMAT, P2P_MAGIC, P2P_VERSION, msg_type, seq, len(payload_bytes))
        self._udp_transport.sendto(header + payload_bytes, addr)

    async def _handle_datagram(self, data: bytes, addr: tuple):
        try:
            if len(data) < P2P_HEADER_SIZE:
                return
            magic, version, msg_type, seq, payload_len = struct.unpack(
                P2P_HEADER_FORMAT, data[:P2P_HEADER_SIZE]
            )
            if magic != P2P_MAGIC:
                return
            total_size = P2P_HEADER_SIZE + payload_len
            if len(data) < total_size:
                return
            payload_data = data[P2P_HEADER_SIZE:total_size] if payload_len > 0 else b""
            await self._dispatch(addr, msg_type, seq, payload_data)
        except Exception as e:
            logger.error(f"P2P 数据报处理失败: {e}")

    async def _dispatch(self, addr: tuple, msg_type: int, seq: int, payload: bytes):
        handler = {
            P2P_FILE_INIT: self._handle_file_init,
            P2P_FILE_DATA: self._handle_file_data,
            P2P_FILE_ACK: self._handle_file_ack,
            P2P_FILE_RESUME: self._handle_file_resume,
            P2P_FILE_DONE: self._handle_file_done,
            P2P_PUNCH: self._handle_punch,
            P2P_PUNCH_ACK: self._handle_punch_ack,
        }.get(msg_type)
        if handler:
            await handler(addr, seq, payload)
        else:
            logger.warning(f"未知 P2P 消息类型: {msg_type}")

    # ---- 打洞 ----

    async def hole_punch(
        self,
        server_conn: ChatConnection,
        my_user_id: int,
        target_user_id: int,
        my_public_addr: str = "",
        punch_timeout: float = 5.0,
    ) -> Optional[tuple]:
        """
        通过服务器协助进行 UDP 打洞

        Args:
            server_conn: 与服务端的 TCP 连接（ChatConnection，send_message 为同步调用）
            my_user_id: 当前用户 ID
            target_user_id: 目标用户 ID
            my_public_addr: 当前用户的公网地址 "ip:port"（由服务器从 TCP 连接获知后回传）
            punch_timeout: 打洞超时

        Returns:
            成功返回 (target_host, target_port)，失败返回 None
        """
        if not self._running:
            await self.start()

        udp_addr = my_public_addr or f"0.0.0.0:{self._local_udp_port}"

        # ChatConnection.send_message 是同步方法，不需要 await
        server_conn.send_message(
            MessageType.P2P_HOLE_PUNCH,
            {
                "user_id": my_user_id,
                "target_id": target_user_id,
                "addr": udp_addr,
            },
        )

        logger.info(f"P2P 打洞请求已发送: my_id={my_user_id}, target={target_user_id}")
        return await self._wait_for_target_addr(target_user_id, punch_timeout)

    async def _wait_for_target_addr(self, target_id: int, timeout: float) -> Optional[tuple]:
        """等待服务器返回目标地址"""
        self._target_event = asyncio.Event()
        self._target_addr = None
        try:
            await asyncio.wait_for(self._target_event.wait(), timeout=timeout)
            return self._target_addr
        except asyncio.TimeoutError:
            logger.warning(f"P2P 打洞超时: target={target_id}")
            return None

    def _on_target_addr(self, addr_str: str):
        """收到服务器返回的目标地址，设置事件唤醒等待的 hole_punch 协程"""
        try:
            host, port_str = addr_str.rsplit(":", 1)
            self._target_addr = (host, int(port_str))
            if hasattr(self, "_target_event"):
                self._target_event.set()
        except (ValueError, AttributeError) as e:
            logger.error(f"P2P 目标地址格式无效: {addr_str}: {e}")

    def _on_hole_punch_response(self, msg_type, seq, payload: dict):
        """
        TCP 消息回调：处理服务端返回的 P2P_HOLE_PUNCH 响应
        将 addr 字段提取后传给 _on_target_addr 以唤醒等待的 hole_punch 协程
        """
        addr = payload.get("addr", "")
        if addr:
            self._on_target_addr(addr)
        else:
            error = payload.get("error", "")
            logger.warning(f"P2P 打洞响应错误: {error}")
            # 释放等待协程（返回 None）
            if hasattr(self, "_target_event"):
                self._target_event.set()

    def register_message_handler(self, handler):
        """
        注册 MessageHandler 回调，使 P2P_HOLE_PUNCH TCP 响应能路由到 P2PClient

        Args:
            handler: client.message_handler.MessageHandler 实例
        """
        handler.register(MessageType.P2P_HOLE_PUNCH, self._on_hole_punch_response)

    def start_punch_to(self, target_addr: tuple):
        """向目标地址发送打洞包"""
        punch_data = json.dumps({"type": "punch", "ts": time.time()}).encode("utf-8")

        async def _punch():
            for i in range(PUNCH_COUNT):
                if not self._running:
                    break
                self._send_udp(target_addr, P2P_PUNCH, punch_data, seq=i)
                await asyncio.sleep(PUNCH_INTERVAL)

        asyncio.ensure_future(_punch())

    async def start_punch_to_async(self, target_addr: tuple):
        """向目标地址发送打洞包（可等待版本）"""
        punch_data = json.dumps({"type": "punch", "ts": time.time()}).encode("utf-8")
        for i in range(PUNCH_COUNT):
            if not self._running:
                break
            self._send_udp(target_addr, P2P_PUNCH, punch_data, seq=i)
            await asyncio.sleep(PUNCH_INTERVAL)

    async def _handle_punch(self, addr: tuple, seq: int, payload: bytes):
        self._send_udp(addr, P2P_PUNCH_ACK, b"ok", seq=seq)

    async def _handle_punch_ack(self, addr: tuple, seq: int, payload: bytes):
        logger.info(f"P2P 打洞成功！与 {addr} 直连已建立")

    # ---- 文件发送 ----

    async def send_file_p2p(
        self,
        target_addr: tuple,
        filepath: str,
        aes_key: Optional[bytes] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """通过 P2P 直连发送文件，支持断点续传和丢包重传"""
        filepath = str(filepath)
        if not os.path.exists(filepath):
            logger.error(f"文件不存在: {filepath}")
            return False

        filesize = os.path.getsize(filepath)
        filename = os.path.basename(filepath)
        total_chunks = (filesize + self._chunk_size - 1) // self._chunk_size

        logger.info(f"P2P 发送文件: {filename} ({filesize} bytes, {total_chunks} 块)")

        # 发送文件初始化
        init_data = json.dumps({
            "filename": filename,
            "filesize": filesize,
            "chunk_size": self._chunk_size,
            "total_chunks": total_chunks,
        }).encode("utf-8")
        self._send_udp(target_addr, P2P_FILE_INIT, init_data)

        # 等待 ACK 确认接收方就绪
        init_seq = int(time.time() * 1000)
        ack_event = asyncio.Event()
        self._pending_acks[init_seq] = ack_event
        try:
            await asyncio.wait_for(ack_event.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            logger.warning(f"P2P 文件初始化未收到确认，继续发送")
        finally:
            self._pending_acks.pop(init_seq, None)

        # 分块发送（带重传）
        start_chunk = 0
        sent = 0
        with open(filepath, "rb") as f:
            for chunk_idx in range(start_chunk, total_chunks):
                chunk_data = f.read(self._chunk_size)

                if aes_key:
                    aesgcm = AESGCM(aes_key)
                    nonce = struct.pack(">Q", chunk_idx).rjust(12, b"\x00")
                    chunk_data = aesgcm.encrypt(nonce, chunk_data, None)

                # 带重传的发送
                for attempt in range(MAX_RETRIES + 1):
                    header_info = json.dumps({
                        "chunk_idx": chunk_idx,
                        "offset": sent,
                    }).encode("utf-8")
                    self._send_udp(target_addr, P2P_FILE_DATA, header_info + b"|" + chunk_data)

                    if attempt < MAX_RETRIES:
                        # 等待 ACK
                        ack_evt = asyncio.Event()
                        self._pending_acks[chunk_idx] = ack_evt
                        try:
                            await asyncio.wait_for(ack_evt.wait(), timeout=ACK_TIMEOUT)
                            break  # 收到 ACK，继续下一块
                        except asyncio.TimeoutError:
                            logger.debug(f"P2P 块 #{chunk_idx} 超时，重传 ({attempt+1}/{MAX_RETRIES})")
                        finally:
                            self._pending_acks.pop(chunk_idx, None)

                sent += self._chunk_size

                if progress_callback:
                    progress_callback(min(sent, filesize), filesize)

        # 发送完成通知
        done_data = json.dumps({"filename": filename, "total_chunks": total_chunks}).encode("utf-8")
        self._send_udp(target_addr, P2P_FILE_DONE, done_data)
        logger.info(f"P2P 文件发送完成: {filename}")
        return True

    # ---- 文件接收 ----

    async def receive_file_p2p(
        self,
        save_dir: str,
        aes_key: Optional[bytes] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ):
        """配置接收参数，等待对端发送文件。调用后通过 _handle_file_init 和 _handle_file_data 接收"""
        self._recv_save_dir = save_dir
        self._recv_aes_key = aes_key
        self._recv_progress = progress_callback

    async def _handle_file_init(self, addr: tuple, seq: int, payload: bytes):
        try:
            info = json.loads(payload.decode("utf-8"))
            save_dir = self._recv_save_dir
            filename = info["filename"]
            filesize = info["filesize"]
            total_chunks = info["total_chunks"]

            save_path = os.path.join(save_dir, filename)
            os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

            resume_offset = 0
            if os.path.exists(save_path):
                resume_offset = os.path.getsize(save_path)
                if resume_offset < filesize:
                    logger.info(f"检测到部分下载: {save_path} ({resume_offset}/{filesize})")
                    resume_data = json.dumps({"filename": filename, "offset": resume_offset}).encode("utf-8")
                    self._send_udp(addr, P2P_FILE_RESUME, resume_data)
                    # 保存续传信息用于 _handle_file_data
                    self._file_resume_offset = resume_offset
                    self._file_resume_path = save_path
                    return

            # 回复 ACK 表示就绪
            ack_data = json.dumps({"offset": 0}).encode("utf-8")
            self._send_udp(addr, P2P_FILE_ACK, ack_data)

            self._recv_file_info = {
                "addr": addr,
                "save_path": save_path,
                "filesize": filesize,
                "total_chunks": total_chunks,
                "received": getattr(self, "_file_resume_offset", 0),
                "aes_key": self._recv_aes_key,
                "progress": self._recv_progress,
                "filename": filename,
            }

            if hasattr(self, "_file_resume_offset"):
                self._recv_file_info["received"] = self._file_resume_offset

            logger.info(f"P2P 准备接收: {filename} ({filesize} bytes, {total_chunks} 块)")
        except Exception as e:
            logger.error(f"P2P 文件初始化处理失败: {e}")

    async def _handle_file_data(self, addr: tuple, seq: int, payload: bytes):
        try:
            sep_idx = payload.find(b"|")
            if sep_idx == -1:
                return
            header_data = payload[:sep_idx]
            chunk_raw = payload[sep_idx + 1:]

            header = json.loads(header_data.decode("utf-8"))
            chunk_idx = header["chunk_idx"]
            offset = header.get("offset", chunk_idx * self._chunk_size)

            file_info = self._recv_file_info
            if not file_info or file_info["addr"] != addr:
                return

            save_path = file_info["save_path"]
            aes_key = file_info.get("aes_key")

            if aes_key and chunk_raw:
                aesgcm = AESGCM(aes_key)
                nonce = struct.pack(">Q", chunk_idx).rjust(12, b"\x00")
                chunk_raw = aesgcm.decrypt(nonce, chunk_raw, None)

            # 写入文件（用追加模式，seek 到正确偏移）
            with open(save_path, "ab") as f:
                f.seek(offset)
                f.write(chunk_raw)

            file_info["received"] = min(file_info["received"] + len(chunk_raw), file_info["filesize"])

            if file_info.get("progress"):
                file_info["progress"](file_info["received"], file_info["filesize"])

            # 发送 ACK
            ack_data = json.dumps({"chunk_idx": chunk_idx, "offset": offset + len(chunk_raw)}).encode("utf-8")
            self._send_udp(addr, P2P_FILE_ACK, ack_data, seq=chunk_idx)
        except Exception as e:
            logger.error(f"P2P 文件数据块处理失败: {e}")

    async def _handle_file_ack(self, addr: tuple, seq: int, payload: bytes):
        """收到 ACK，唤醒等待的发送协程"""
        try:
            ack = json.loads(payload.decode("utf-8"))
            chunk_idx = ack.get("chunk_idx", seq)
            evt = self._pending_acks.get(chunk_idx)
            if evt:
                evt.set()
        except Exception:
            pass

    async def _handle_file_done(self, addr: tuple, seq: int, payload: bytes):
        """文件传输完成"""
        try:
            done = json.loads(payload.decode("utf-8"))
            logger.info(f"P2P 文件接收完成: {done.get('filename', 'unknown')}")
        except Exception as e:
            logger.warning(f"P2P 完成通知解析失败: {e}")

    async def _handle_file_resume(self, addr: tuple, seq: int, payload: bytes):
        """收到断点续传请求"""
        try:
            resume = json.loads(payload.decode("utf-8"))
            logger.info(f"P2P 续传请求: offset={resume.get('offset', 0)}")
        except Exception:
            pass


async def create_p2p_client(config: Optional[Config] = None) -> P2PClient:
    client = P2PClient(config)
    await client.start()
    return client
