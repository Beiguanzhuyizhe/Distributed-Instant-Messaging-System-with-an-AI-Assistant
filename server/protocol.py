"""
分布式即时聊天系统 - 通信协议定义与编解码模块
Magic(2B) + Version(1B) + Type(1B) + Seq(4B) + PayloadLen(4B) = 12B header + JSON payload

TCP 粘包处理：使用长度前缀方式，每次从 buffer 中提取完整消息。
"""

import asyncio
import json
import struct
from enum import IntEnum

MAGIC = 0xCAFE
VERSION = 0x01
HEADER_FORMAT = "!H B B I I"  # Magic:H(2B) Version:B(1B) Type:B(1B) Seq:I(4B) PayloadLen:I(4B)
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # 12 bytes


class MessageType(IntEnum):
    # 认证
    LOGIN_REQ = 0x01        # C->S 登录请求
    LOGIN_RESP = 0x02       # S->C 登录响应
    REGISTER_REQ = 0x03     # C->S 注册请求
    REGISTER_RESP = 0x04    # S->C 注册响应

    # 消息
    PRIVATE_MSG = 0x05      # C<->S 私聊消息
    GROUP_MSG = 0x06        # C<->S 群聊消息

    # 心跳
    HEARTBEAT = 0x07        # C->S 心跳包
    HEARTBEAT_ACK = 0x08    # S->C 心跳确认

    # 文件传输 (中继模式)
    FILE_INIT = 0x09        # C<->S 文件传输初始化
    FILE_DATA = 0x0A        # C<->S 文件数据块
    FILE_ACK = 0x0B         # C<->S 文件块确认

    # 群组管理
    GROUP_CREATE = 0x0C     # C<->S 创建群组
    GROUP_JOIN = 0x0D       # C<->S 加入群组
    GROUP_LEAVE = 0x0E      # C<->S 退出群组

    # 状态与通知
    STATUS_UPDATE = 0x0F    # S->C 在线状态推送
    MSG_RECALL = 0x10       # C<->S 消息撤回

    # AI
    AI_QUERY = 0x11         # C->S @AI 查询
    AI_RESP = 0x12          # S->C AI 回复
    CONTENT_WARN = 0x13     # S->C 内容违规警告

    # 历史消息
    HISTORY_REQ = 0x14      # C->S 历史消息请求
    HISTORY_RESP = 0x15     # S->C 历史消息响应

    # 在线用户
    ONLINE_USERS = 0x16     # C<->S 在线用户列表

    # P2P 打洞
    P2P_HOLE_PUNCH = 0x17   # C<->S P2P 打洞协助
    P2P_READY = 0x18        # C<->S P2P 就绪通知

    # 错误
    ERROR = 0xFF            # S->C 错误响应


# 错误码
class ErrorCode(IntEnum):
    SUCCESS = 0
    INVALID_REQUEST = 1
    AUTH_FAILED = 2
    USER_EXISTS = 3
    USER_NOT_FOUND = 4
    GROUP_NOT_FOUND = 5
    NOT_GROUP_MEMBER = 6
    MESSAGE_TOO_LARGE = 7
    FILE_TOO_LARGE = 8
    RATE_LIMITED = 9
    INTERNAL_ERROR = 10
    P2P_FAILED = 11
    MSG_NOT_FOUND = 12
    RECALL_TIMEOUT = 13
    CONTENT_REJECTED = 14
    INVALID_PAYLOAD = 15


class SequenceGenerator:
    """全局序列号生成器"""
    _seq = 0

    @classmethod
    def next(cls) -> int:
        cls._seq = (cls._seq + 1) & 0xFFFFFFFF
        return cls._seq


def encode_message(msg_type: int, payload: dict, seq: int = None) -> bytes:
    """
    编码消息为二进制格式。
    返回完整的二进制包：header + payload_json
    """
    if seq is None:
        seq = SequenceGenerator.next()

    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload_len = len(payload_bytes)

    header = struct.pack(HEADER_FORMAT, MAGIC, VERSION, msg_type, seq, payload_len)
    return header + payload_bytes


def decode_message(data: bytes):
    """
    从二进制数据中解码出一条消息。
    Returns:
        (msg_type, seq, payload_dict, consumed_bytes)
        如果数据不足一个完整消息，consumed_bytes = 0，前三个返回 None。
        如果解析成功，consumed_bytes 为实际消耗字节数。
    """
    if len(data) < HEADER_SIZE:
        return None, 0, None, 0

    magic, version, msg_type, seq, payload_len = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])

    if magic != MAGIC:
        raise ValueError(f"Invalid magic: {magic:#x}, expected {MAGIC:#x}")

    total_size = HEADER_SIZE + payload_len
    if len(data) < total_size:
        return None, 0, None, 0  # 不完整包，等待更多数据

    if payload_len > 0:
        payload_bytes = data[HEADER_SIZE:total_size]
        payload = json.loads(payload_bytes.decode("utf-8"))
    else:
        payload = {}

    return msg_type, seq, payload, total_size


def decode_messages(data: bytes):
    """
    从字节流中解码所有完整消息（处理粘包）。
    Returns:
        (messages, remaining_bytes)
        messages: [(msg_type, seq, payload), ...]
        remaining_bytes: 未消费的字节（不完整包尾部）
    """
    messages = []
    offset = 0
    data_len = len(data)

    while offset < data_len:
        chunk = data[offset:]
        if len(chunk) < HEADER_SIZE:
            break

        msg_type, seq, payload, consumed = decode_message(chunk)
        if consumed == 0:
            break  # 不完整包

        messages.append((msg_type, seq, payload))
        offset += consumed

    return messages, data[offset:]


# ============================================================
# T13: TCP 协议编解码增强
# ============================================================

class MessageProtocol:
    """
    处理 TCP 粘包/半包的消息协议处理器。
    内部维护一个 buffer，每次调用 feed(data) 将新数据加入，
    然后通过 next_message() 尝试提取完整消息。
    """

    def __init__(self):
        self._buffer = bytearray()
        self._msg_count = 0

    def feed(self, data: bytes):
        """将接收到的字节数据喂入 buffer"""
        self._buffer.extend(data)

    @property
    def buffered_bytes(self) -> int:
        return len(self._buffer)

    @property
    def message_count(self) -> int:
        return self._msg_count

    def next_message(self):
        """
        尝试从 buffer 中提取一条完整消息。
        Returns:
            (msg_type, seq, payload) | None
            如果没有完整消息则返回 None。
        """
        if len(self._buffer) < HEADER_SIZE:
            return None

        total_size = self._get_message_total_size()
        if total_size is None:
            return None

        if len(self._buffer) < total_size:
            return None  # 数据还不够

        chunk = bytes(self._buffer[:total_size])
        self._buffer = self._buffer[total_size:]

        msg_type, seq, payload, _ = decode_message(chunk)
        self._msg_count += 1
        return msg_type, seq, payload

    def next_messages(self):
        """
        提取 buffer 中所有完整消息。
        Returns:
            [(msg_type, seq, payload), ...]
        """
        msgs = []
        while True:
            msg = self.next_message()
            if msg is None:
                break
            msgs.append(msg)
        return msgs

    def _get_message_total_size(self):
        """估算当前 buffer 中第一条消息的完整长度（含 header）。"""
        if len(self._buffer) < HEADER_SIZE:
            return None
        _, _, _, _, payload_len = struct.unpack(HEADER_FORMAT, self._buffer[:HEADER_SIZE])
        return HEADER_SIZE + payload_len

    def reset(self):
        """清空 buffer"""
        self._buffer.clear()
        self._msg_count = 0


class Connection:
    """
    封装 asyncio socket 连接的异步读写。
    提供 send_message / read_message 高阶接口。
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 protocol: MessageProtocol = None):
        self.reader = reader
        self.writer = writer
        self.protocol = protocol or MessageProtocol()
        self._closed = False
        self._remote_addr = writer.get_extra_info("peername") if writer else None

    @property
    def remote_addr(self):
        return self._remote_addr

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def send_message(self, msg_type: int, payload: dict, seq: int = None):
        """异步发送一条编码后的消息"""
        if self._closed:
            raise ConnectionError("Connection is closed")
        data = encode_message(msg_type, payload, seq)
        self.writer.write(data)
        await self.writer.drain()

    async def read_message(self):
        """
        异步读取一条完整消息。内部自动处理粘包/半包。
        Returns:
            (msg_type, seq, payload) | None (连接关闭)
        """
        while True:
            msg = self.protocol.next_message()
            if msg is not None:
                return msg

            try:
                chunk = await self.reader.read(4096)
            except (ConnectionError, OSError):
                self._closed = True
                return None

            if not chunk:
                # 连接关闭
                self._closed = True
                # 检查 buffer 里是否还有残留消息
                msg = self.protocol.next_message()
                return msg

            self.protocol.feed(chunk)

    async def read_until_timeout(self, timeout: float = None):
        """
        带超时的单条消息读取。
        Returns:
            (msg_type, seq, payload) | None (超时或连接关闭)
        """
        try:
            return await asyncio.wait_for(self.read_message(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def send_heartbeat(self):
        """发送心跳包"""
        await self.send_message(MessageType.HEARTBEAT, {})

    def close(self):
        """关闭连接"""
        if not self._closed:
            self._closed = True
            try:
                self.writer.close()
            except Exception:
                pass

    async def wait_closed(self):
        """等待连接完全关闭"""
        try:
            await self.writer.wait_closed()
        except Exception:
            pass


# ============================================================
# Payload 构建辅助函数
# ============================================================

def make_login_payload(username: str, password_hash: str) -> dict:
    return {"username": username, "password_hash": password_hash}


def make_register_payload(username: str, password_hash: str, public_key: str = "") -> dict:
    return {"username": username, "password_hash": password_hash, "public_key": public_key}


def make_private_msg_payload(from_id: int, to_id: int, content: str,
                             msg_id: int = 0, timestamp: int = 0) -> dict:
    return {
        "from_id": from_id,
        "to_id": to_id,
        "content": content,
        "msg_id": msg_id,
        "timestamp": timestamp or _now(),
    }


def make_group_msg_payload(from_id: int, group_id: int, content: str,
                           msg_id: int = 0, timestamp: int = 0) -> dict:
    return {
        "from_id": from_id,
        "group_id": group_id,
        "content": content,
        "msg_id": msg_id,
        "timestamp": timestamp or _now(),
    }


def make_error_payload(code: int, message: str) -> dict:
    return {"code": code, "message": message}


def make_p2p_hole_punch_payload(user_id: int, target_id: int, addr: str = "") -> dict:
    return {"user_id": user_id, "target_id": target_id, "addr": addr}


def make_ai_query_payload(user_id: int, group_id: int, query: str,
                          msg_id: int = 0) -> dict:
    payload = {"user_id": user_id, "from_id": user_id, "group_id": group_id, "query": query}
    if msg_id:
        payload["msg_id"] = msg_id
    return payload


def make_ai_resp_payload(group_id: int, content: str, user_id: int = 0,
                         query: str = "", msg_id: int = 0) -> dict:
    payload = {"group_id": group_id, "content": content, "reply": content}
    if user_id:
        payload["user_id"] = user_id
        payload["from_id"] = user_id
    if query:
        payload["query"] = query
    if msg_id:
        payload["msg_id"] = msg_id
    return payload


def make_content_warn_payload(user_id: int, reason: str, level: str = "mid",
                              msg_id: int = 0) -> dict:
    payload = {"user_id": user_id, "reason": reason, "message": reason, "level": level}
    if msg_id:
        payload["msg_id"] = msg_id
    return payload


def make_file_init_payload(from_id: int, filename: str, filesize: int,
                           file_id: str = "", to_id: int = 0,
                           group_id: int = 0) -> dict:
    payload = {
        "from_id": from_id,
        "filename": filename,
        "filesize": filesize,
        "file_id": file_id,
    }
    if to_id:
        payload["to_id"] = to_id
    if group_id:
        payload["group_id"] = group_id
    return payload


def make_file_data_payload(file_id: str, offset: int, data: str,
                           is_last: bool = False) -> dict:
    return {"file_id": file_id, "offset": offset, "data": data, "is_last": is_last}


def make_file_ack_payload(file_id: str, offset: int, received: int,
                          success: bool = True) -> dict:
    return {"file_id": file_id, "offset": offset, "received": received, "success": success}


def make_recall_payload(msg_id, user_id: int = 0) -> dict:
    payload = {"msg_id": msg_id}
    if user_id:
        payload["user_id"] = user_id
    return payload


def _now() -> int:
    import time
    return int(time.time())
