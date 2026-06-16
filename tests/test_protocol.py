"""
协议编解码单元测试
"""

import json
import struct

import pytest

from server.protocol import (
    HEADER_FORMAT,
    HEADER_SIZE,
    MAGIC,
    VERSION,
    Connection,
    ErrorCode,
    MessageProtocol,
    MessageType,
    SequenceGenerator,
    decode_message,
    decode_messages,
    encode_message,
    make_ai_query_payload,
    make_ai_resp_payload,
    make_content_warn_payload,
    make_error_payload,
    make_file_ack_payload,
    make_file_data_payload,
    make_file_init_payload,
    make_group_msg_payload,
    make_login_payload,
    make_p2p_hole_punch_payload,
    make_private_msg_payload,
    make_recall_payload,
    make_register_payload,
)


# ===================================================================
# 消息类型常量测试
# ===================================================================


class TestMessageType:
    """MessageType 枚举值验证"""

    def test_constants_values(self):
        assert MessageType.LOGIN_REQ == 0x01
        assert MessageType.LOGIN_RESP == 0x02
        assert MessageType.REGISTER_REQ == 0x03
        assert MessageType.REGISTER_RESP == 0x04
        assert MessageType.PRIVATE_MSG == 0x05
        assert MessageType.GROUP_MSG == 0x06
        assert MessageType.HEARTBEAT == 0x07
        assert MessageType.HEARTBEAT_ACK == 0x08
        assert MessageType.FILE_INIT == 0x09
        assert MessageType.FILE_DATA == 0x0A
        assert MessageType.FILE_ACK == 0x0B
        assert MessageType.GROUP_CREATE == 0x0C
        assert MessageType.GROUP_JOIN == 0x0D
        assert MessageType.GROUP_LEAVE == 0x0E
        assert MessageType.STATUS_UPDATE == 0x0F
        assert MessageType.MSG_RECALL == 0x10
        assert MessageType.AI_QUERY == 0x11
        assert MessageType.AI_RESP == 0x12
        assert MessageType.CONTENT_WARN == 0x13
        assert MessageType.HISTORY_REQ == 0x14
        assert MessageType.HISTORY_RESP == 0x15
        assert MessageType.ONLINE_USERS == 0x16
        assert MessageType.P2P_HOLE_PUNCH == 0x17
        assert MessageType.P2P_READY == 0x18
        assert MessageType.ERROR == 0xFF

    def test_no_duplicate_values(self):
        """枚举值无重复"""
        values = [mt.value for mt in MessageType]
        assert len(values) == len(set(values))

    def test_all_values_in_range(self):
        """所有枚举值在 0x01~0xFF 范围内"""
        for mt in MessageType:
            assert 1 <= mt.value <= 0xFF


# ===================================================================
# 编码测试
# ===================================================================


class TestEncode:
    def test_header_size(self):
        assert HEADER_SIZE == 12

    def test_encode_login(self):
        payload = {"username": "alice", "password": "secret"}
        data = encode_message(MessageType.LOGIN_REQ, payload, seq=1)
        assert len(data) > HEADER_SIZE

        magic, version, msg_type, seq, payload_len = struct.unpack(
            HEADER_FORMAT, data[:HEADER_SIZE]
        )
        assert magic == MAGIC
        assert version == VERSION
        assert msg_type == MessageType.LOGIN_REQ
        assert seq == 1
        assert payload_len > 0

    def test_encode_all_message_types(self):
        """所有消息类型都能正确编码"""
        for mt in MessageType:
            data = encode_message(mt.value, {"test": True}, seq=0)
            magic, _, msg_type, _, _ = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
            assert magic == MAGIC
            assert msg_type == mt.value

    def test_encode_unicode_content(self):
        """中文/Unicode 内容编码正确"""
        payload = {"content": "你好世界！"}
        data = encode_message(MessageType.PRIVATE_MSG, payload, seq=1)

        _, _, _, _, payload_len = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
        assert payload_len > 0

        decoded = json.loads(data[HEADER_SIZE:])
        assert decoded["content"] == "你好世界！"

    def test_encode_nested_payload(self):
        """嵌套 JSON payload 编码"""
        payload = {
            "user": {"name": "alice", "roles": ["admin", "user"]},
            "meta": {"online": True, "count": 42},
        }
        data = encode_message(MessageType.STATUS_UPDATE, payload, seq=5)
        decoded = json.loads(data[HEADER_SIZE:])
        assert decoded == payload

    def test_encode_compact_json(self):
        """编码使用紧凑 JSON（无多余空格）"""
        payload = {"key": "value", "num": 42}
        data = encode_message(MessageType.LOGIN_REQ, payload, seq=1)
        payload_raw = data[HEADER_SIZE:].decode("utf-8")
        # 紧凑 JSON: {"key":"value","num":42} — 无空格
        assert " " not in payload_raw


# ===================================================================
# 解码测试
# ===================================================================


class TestDecode:
    def test_roundtrip(self):
        payload = {"username": "alice", "password": "pass123"}
        data = encode_message(MessageType.LOGIN_REQ, payload, seq=42)
        msg_type, seq, decoded, consumed = decode_message(data)
        assert msg_type == MessageType.LOGIN_REQ
        assert seq == 42
        assert decoded["username"] == "alice"
        assert decoded["password"] == "pass123"
        assert consumed == len(data)

    def test_empty_payload(self):
        data = encode_message(MessageType.HEARTBEAT, {}, seq=1)
        msg_type, seq, decoded, consumed = decode_message(data)
        assert msg_type == MessageType.HEARTBEAT
        assert decoded == {}
        assert consumed == len(data)

    def test_invalid_magic(self):
        payload = {"test": "data"}
        data = encode_message(MessageType.PRIVATE_MSG, payload, seq=1)
        bad_data = bytearray(data)
        bad_data[0] = 0xDE
        bad_data[1] = 0xAD
        with pytest.raises(ValueError, match="Invalid magic"):
            decode_message(bytes(bad_data))

    def test_incomplete_header_returns_none(self):
        """不完整 header 返回 (None, 0, None, 0) 而不是抛异常"""
        result = decode_message(b"\x00" * 5)
        assert result == (None, 0, None, 0)

    def test_incomplete_payload(self):
        payload = {"data": "x" * 1000}
        data = encode_message(MessageType.PRIVATE_MSG, payload, seq=1)
        truncated = data[: HEADER_SIZE + 10]
        result = decode_message(truncated)
        assert result == (None, 0, None, 0)

    def test_roundtrip_all_payload_helpers(self):
        """使用所有 payload helper 函数构造 payload，编解码后数据一致"""
        test_cases = [
            make_login_payload("user1", "pass1_hash"),
            make_register_payload("u2", "p2_hash", "pubkey123"),
            make_private_msg_payload(1, 2, "hi", msg_id=1001, timestamp=1),
            make_group_msg_payload(1, 42, "hi all", msg_id=2001, timestamp=2),
            make_error_payload(1, "bad request"),
            make_p2p_hole_punch_payload(1, 2, "10.0.0.1:9000"),
        ]

        for payload in test_cases:
            for mt in (MessageType.LOGIN_REQ, MessageType.PRIVATE_MSG, MessageType.ERROR):
                encoded = encode_message(mt.value, payload, seq=1)
                _, _, decoded, consumed = decode_message(encoded)
                assert decoded == payload, f"Failed for {mt.name} with {payload}"
                assert consumed == len(encoded)

    def test_special_chars_roundtrip(self):
        """特殊字符（引号、反斜杠、控制字符）编解码"""
        special = {"text": 'hello "world" \'quoted\' \\backslash \n\t\r'}
        encoded = encode_message(MessageType.PRIVATE_MSG, special, seq=1)
        _, _, decoded, _ = decode_message(encoded)
        assert decoded == special


# ===================================================================
# 粘包/半包测试
# ===================================================================


class TestStickyPackets:
    def test_multiple_messages(self):
        data1 = encode_message(MessageType.HEARTBEAT, {"ts": 1}, seq=1)
        data2 = encode_message(MessageType.HEARTBEAT, {"ts": 2}, seq=2)
        combined = data1 + data2

        messages, remaining = decode_messages(combined)
        assert len(messages) == 2
        assert messages[0][0] == MessageType.HEARTBEAT
        assert messages[0][1] == 1
        assert messages[1][1] == 2
        assert remaining == b""

    def test_partial_last_message(self):
        data1 = encode_message(MessageType.LOGIN_REQ, {"u": "a"}, seq=1)
        data2 = encode_message(MessageType.HEARTBEAT, {}, seq=2)
        partial = data2[: HEADER_SIZE + 1]
        combined = data1 + partial

        messages, remaining = decode_messages(combined)
        assert len(messages) == 1
        assert remaining == partial

    def test_three_messages_sticky(self):
        """三条消息粘包解码"""
        msgs = [
            encode_message(MessageType.LOGIN_REQ, {"u": "a"}, seq=1),
            encode_message(MessageType.HEARTBEAT, {}, seq=2),
            encode_message(MessageType.PRIVATE_MSG, {"text": "hi"}, seq=3),
        ]
        combined = b"".join(msgs)

        results, remaining = decode_messages(combined)
        assert len(results) == 3
        assert results[1] == (MessageType.HEARTBEAT, 2, {})
        assert remaining == b""

    def test_decode_messages_empty(self):
        """空数据返回空列表"""
        results, remaining = decode_messages(b"")
        assert results == []
        assert remaining == b""

    def test_decode_messages_only_header_size_messages(self):
        """仅含 HEARTBEAT (payload为空) 的多条消息粘包"""
        data1 = encode_message(MessageType.HEARTBEAT, {}, seq=1)
        data2 = encode_message(MessageType.HEARTBEAT_ACK, {}, seq=2)
        data3 = encode_message(MessageType.HEARTBEAT, {}, seq=3)
        combined = data1 + data2 + data3

        results, remaining = decode_messages(combined)
        assert len(results) == 3


# ===================================================================
# SequenceGenerator 测试
# ===================================================================


class TestSequenceGenerator:
    def test_sequence_increment(self):
        s1 = SequenceGenerator.next()
        s2 = SequenceGenerator.next()
        assert s2 == (s1 + 1) & 0xFFFFFFFF

    def test_auto_sequence_in_encode(self):
        data1 = encode_message(MessageType.HEARTBEAT, {})
        data2 = encode_message(MessageType.HEARTBEAT, {})
        _, seq1, _, _ = decode_message(data1)
        _, seq2, _, _ = decode_message(data2)
        assert seq2 == (seq1 + 1) & 0xFFFFFFFF

    def test_overflow(self):
        SequenceGenerator._seq = 0xFFFFFFFF
        assert SequenceGenerator.next() == 0

    def test_32bit_bound(self):
        SequenceGenerator._seq = 0x7FFFFFFF
        val = SequenceGenerator.next()
        assert 0 <= val <= 0xFFFFFFFF


# ===================================================================
# 边界条件测试
# ===================================================================


class TestEdgeCases:
    def test_zero_length_input(self):
        """空字节串解码"""
        msg_type, seq, payload, consumed = decode_message(b"")
        assert msg_type is None
        assert consumed == 0

    def test_large_payload_roundtrip(self):
        """大 payload（~900KB）编解码正确"""
        large = {"data": "x" * 900_000}
        encoded = encode_message(MessageType.PRIVATE_MSG, large, seq=1)
        msg_type, seq, decoded, consumed = decode_message(encoded)

        assert msg_type == MessageType.PRIVATE_MSG
        assert decoded["data"] == large["data"]
        assert len(decoded["data"]) == 900_000

    def test_exact_header_size_only(self):
        """只有 header 大小（payload_len=0）可以正常解码"""
        header = struct.pack(HEADER_FORMAT, MAGIC, VERSION, MessageType.HEARTBEAT, 1, 0)
        msg_type, seq, payload, consumed = decode_message(header)
        assert msg_type == MessageType.HEARTBEAT
        assert payload == {}
        assert consumed == HEADER_SIZE


# ===================================================================
# Payload helper 函数测试
# ===================================================================


class TestPayloadHelpers:
    def test_make_login_payload(self):
        p = make_login_payload("alice", "hash123")
        assert p == {"username": "alice", "password_hash": "hash123"}

    def test_make_register_payload_without_key(self):
        p = make_register_payload("bob", "pwd_hash")
        assert p == {"username": "bob", "password_hash": "pwd_hash", "public_key": ""}

    def test_make_register_payload_with_key(self):
        p = make_register_payload("bob", "pwd_hash", "key123")
        assert p["public_key"] == "key123"

    def test_make_private_msg_payload(self):
        p = make_private_msg_payload(1, 2, "hello", msg_id=101, timestamp=1000)
        assert p["from_id"] == 1
        assert p["to_id"] == 2
        assert p["content"] == "hello"
        assert p["msg_id"] == 101

    def test_make_private_msg_default_timestamp(self):
        """不传 timestamp 时自动生成"""
        import time
        now = int(time.time())
        p = make_private_msg_payload(1, 2, "hi")
        assert p["timestamp"] >= now - 1

    def test_make_group_msg_payload(self):
        p = make_group_msg_payload(1, 42, "大家好", msg_id=201, timestamp=100)
        assert p["from_id"] == 1
        assert p["group_id"] == 42
        assert p["content"] == "大家好"
        assert p["msg_id"] == 201

    def test_make_error_payload(self):
        p = make_error_payload(2, "Auth failed")
        assert p == {"code": 2, "message": "Auth failed"}

    def test_make_p2p_hole_punch_payload(self):
        p = make_p2p_hole_punch_payload(1, 2, "10.0.0.1:9000")
        assert p["user_id"] == 1
        assert p["target_id"] == 2
        assert p["addr"] == "10.0.0.1:9000"

    def test_make_ai_query_payload(self):
        p = make_ai_query_payload(1, 3, "解释 TCP 粘包", msg_id=9)
        assert p == {
            "user_id": 1,
            "from_id": 1,
            "group_id": 3,
            "query": "解释 TCP 粘包",
            "msg_id": 9,
        }

    def test_make_ai_resp_payload(self):
        p = make_ai_resp_payload(3, "回答内容", user_id=1, query="问题", msg_id=10)
        assert p["content"] == "回答内容"
        assert p["reply"] == "回答内容"
        assert p["query"] == "问题"

    def test_make_content_warn_payload(self):
        p = make_content_warn_payload(1, "包含违规词汇", level="high", msg_id=11)
        assert p["user_id"] == 1
        assert p["reason"] == "包含违规词汇"
        assert p["message"] == "包含违规词汇"
        assert p["level"] == "high"

    def test_make_file_payloads(self):
        init = make_file_init_payload(1, "a.txt", 12, file_id="f1", to_id=2)
        data = make_file_data_payload("f1", 0, "YWJj", is_last=True)
        ack = make_file_ack_payload("f1", 3, 3)

        assert init["to_id"] == 2
        assert data == {"file_id": "f1", "offset": 0, "data": "YWJj", "is_last": True}
        assert ack == {"file_id": "f1", "offset": 3, "received": 3, "success": True}

    def test_make_recall_payload(self):
        assert make_recall_payload("m1", user_id=1) == {"msg_id": "m1", "user_id": 1}


# ===================================================================
# ErrorCode 枚举测试
# ===================================================================


class TestErrorCode:
    def test_error_code_values(self):
        assert ErrorCode.SUCCESS == 0
        assert ErrorCode.AUTH_FAILED == 2
        assert ErrorCode.INTERNAL_ERROR == 10
        assert ErrorCode.CONTENT_REJECTED == 14

    def test_no_duplicate_values(self):
        values = [ec.value for ec in ErrorCode]
        assert len(values) == len(set(values))

    def test_all_values_in_range(self):
        for ec in ErrorCode:
            assert 0 <= ec.value <= 100


# ===================================================================
# MessageProtocol 测试
# ===================================================================


class TestMessageProtocol:
    """MessageProtocol 粘包处理器测试"""

    def test_init_empty(self):
        p = MessageProtocol()
        assert p.buffered_bytes == 0
        assert p.message_count == 0
        assert p.next_message() is None

    def test_feed_and_next_message(self):
        p = MessageProtocol()
        data = encode_message(MessageType.LOGIN_REQ, {"u": "a"}, seq=1)
        p.feed(data)
        assert p.buffered_bytes == len(data)
        msg = p.next_message()
        assert msg is not None
        msg_type, seq, payload = msg
        assert msg_type == MessageType.LOGIN_REQ
        assert payload == {"u": "a"}
        assert p.buffered_bytes == 0
        assert p.message_count == 1

    def test_feed_partial_then_complete(self):
        p = MessageProtocol()
        full = encode_message(MessageType.HEARTBEAT, {}, seq=5)
        header = full[:HEADER_SIZE]
        payload_bytes = full[HEADER_SIZE:]
        p.feed(header)  # 只有 header
        assert p.next_message() is None
        p.feed(payload_bytes)  # 再喂 payload 部分
        msg = p.next_message()
        assert msg is not None
        assert msg[0] == MessageType.HEARTBEAT

    def test_feed_incomplete_payload(self):
        p = MessageProtocol()
        full = encode_message(MessageType.PRIVATE_MSG, {"data": "x" * 100}, seq=1)
        p.feed(full[:HEADER_SIZE + 5])  # header + 部分 payload
        assert p.next_message() is None
        p.feed(full[HEADER_SIZE + 5:])  # 剩余部分
        msg = p.next_message()
        assert msg is not None

    def test_next_messages_multiple(self):
        p = MessageProtocol()
        msgs_data = [
            encode_message(MessageType.HEARTBEAT, {}, seq=1),
            encode_message(MessageType.HEARTBEAT_ACK, {}, seq=2),
        ]
        p.feed(b"".join(msgs_data))
        results = p.next_messages()
        assert len(results) == 2
        assert p.message_count == 2

    def test_feed_interleaved(self):
        """多次 feed 叠加数据"""
        p = MessageProtocol()
        p.feed(encode_message(MessageType.LOGIN_REQ, {"x": 1}, seq=1))
        p.feed(encode_message(MessageType.HEARTBEAT, {}, seq=2))
        results = p.next_messages()
        assert len(results) == 2

    def test_reset_clears_buffer(self):
        p = MessageProtocol()
        p.feed(encode_message(MessageType.HEARTBEAT, {}, seq=1))
        p.reset()
        assert p.buffered_bytes == 0
        assert p.message_count == 0
        assert p.next_message() is None

    def test_message_count_tracking(self):
        p = MessageProtocol()
        p.feed(encode_message(MessageType.HEARTBEAT, {}, seq=1))
        p.next_message()
        p.feed(encode_message(MessageType.HEARTBEAT, {}, seq=2))
        p.feed(encode_message(MessageType.HEARTBEAT, {}, seq=3))
        p.next_messages()
        assert p.message_count == 3
