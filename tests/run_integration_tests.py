"""
End-to-end integration smoke test for the chat server.

Usage:
  1. Start the server: python -m server.main
  2. Run this script: python tests/run_integration_tests.py

This file intentionally does not start with test_ so pytest will not collect it.
"""

import os
import socket
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.protocol import MessageType, decode_messages, encode_message


HOST = "127.0.0.1"
PORT = 8888
TIMEOUT = 5
SUFFIX = uuid.uuid4().hex[:8]


class Client:
    def __init__(self, name):
        self.name = name
        self.sock = None
        self.user_id = None
        self._buffer = b""
        self._inbox = []

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(TIMEOUT)
        self.sock.connect((HOST, PORT))

    def send(self, msg_type, payload, seq=None):
        self.sock.sendall(encode_message(msg_type, payload, seq=seq))

    def recv(self, expected_type=None, predicate=None, timeout=TIMEOUT):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for idx, msg in enumerate(list(self._inbox)):
                msg_type, seq, payload = msg
                if expected_type is not None and msg_type != expected_type:
                    continue
                if predicate is not None and not predicate(payload):
                    continue
                self._inbox.pop(idx)
                return {"type": msg_type, "seq": seq, "payload": payload}

            remaining = max(0.05, deadline - time.time())
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                return None
            if not chunk:
                return None

            self._buffer += chunk
            messages, self._buffer = decode_messages(self._buffer)
            self._inbox.extend(messages)

        return None

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None


passed = 0
failed = 0
u1 = Client("user1")
u2 = Client("user2")
U1_NAME = f"alice_{SUFFIX}"
U2_NAME = f"bob_{SUFFIX}"
group_id = None
private_msg_id = None


def test_step(n, desc, func):
    global passed, failed
    print(f"\n--- Test {n}: {desc} ---")
    try:
        func()
        print("  [PASS]")
        passed += 1
    except AssertionError as exc:
        print(f"  [FAIL] assertion: {exc}")
        failed += 1
    except Exception as exc:
        print(f"  [FAIL] {type(exc).__name__}: {exc}")
        failed += 1


def t1_connect():
    u1.connect()
    u2.connect()


def t2_register():
    u1.send(MessageType.REGISTER_REQ, {"username": U1_NAME, "password_hash": "pass123"})
    resp = u1.recv(MessageType.REGISTER_RESP)
    assert resp, "u1 register response missing"
    assert resp["payload"].get("success"), resp
    u1.user_id = resp["payload"].get("user_id")
    assert u1.user_id is not None

    u2.send(MessageType.REGISTER_REQ, {"username": U2_NAME, "password_hash": "pass456"})
    resp = u2.recv(MessageType.REGISTER_RESP)
    assert resp, "u2 register response missing"
    assert resp["payload"].get("success"), resp
    u2.user_id = resp["payload"].get("user_id")
    assert u2.user_id is not None
    assert u1.user_id != u2.user_id
    print(f"  u1={u1.user_id}, u2={u2.user_id}")


def t3_login():
    u1.send(MessageType.LOGIN_REQ, {"username": U1_NAME, "password_hash": "pass123"})
    resp = u1.recv(MessageType.LOGIN_RESP)
    assert resp and resp["payload"].get("success"), resp

    u2.send(MessageType.LOGIN_REQ, {"username": U2_NAME, "password_hash": "pass456"})
    resp = u2.recv(MessageType.LOGIN_RESP)
    assert resp and resp["payload"].get("success"), resp


def t4_private_msg():
    global private_msg_id
    content = f"Hello Bob {SUFFIX}"
    u1.send(MessageType.PRIVATE_MSG, {"to_id": u2.user_id, "content": content})
    ack = u1.recv(
        MessageType.PRIVATE_MSG,
        predicate=lambda p: p.get("_ack") is True and p.get("status") in {"delivered", "stored"},
    )
    assert ack, "sender did not receive private message ACK"
    private_msg_id = ack["payload"].get("msg_id")
    assert private_msg_id, ack

    delivered = u2.recv(
        MessageType.PRIVATE_MSG,
        predicate=lambda p: p.get("from_id") == u1.user_id and p.get("content") == content,
    )
    assert delivered, "receiver did not receive private message"


def t5_create_group():
    global group_id
    u1.send(MessageType.GROUP_CREATE, {"name": f"TestGroup_{SUFFIX}"})
    resp = u1.recv(MessageType.GROUP_CREATE)
    assert resp, "group create response missing"
    assert resp["payload"].get("success"), resp
    group_id = resp["payload"].get("group_id")
    assert group_id is not None
    print(f"  group_id={group_id}")


def t6_join_group():
    assert group_id is not None
    u2.send(MessageType.GROUP_JOIN, {"group_id": group_id})
    resp = u2.recv(MessageType.GROUP_JOIN)
    assert resp, "group join response missing"
    assert resp["payload"].get("success"), resp


def t7_group_msg():
    assert group_id is not None
    content = f"Hello group {SUFFIX}"
    u2.send(MessageType.GROUP_MSG, {"group_id": group_id, "content": content})
    ack = u2.recv(
        MessageType.GROUP_MSG,
        predicate=lambda p: p.get("_ack") is True and p.get("status") == "sent",
    )
    assert ack, "sender did not receive group message ACK"

    delivered = u1.recv(
        MessageType.GROUP_MSG,
        predicate=lambda p: p.get("group_id") == group_id and p.get("content") == content,
    )
    assert delivered, "group member did not receive group message"


def t8_online_users():
    u1.send(MessageType.ONLINE_USERS, {})
    resp = u1.recv(MessageType.ONLINE_USERS)
    assert resp, "online users response missing"
    users = resp["payload"].get("users", [])
    usernames = {u["username"] for u in users}
    assert U1_NAME in usernames
    assert U2_NAME in usernames
    print(f"  online={len(users)}")


def t9_recall_msg():
    assert private_msg_id, "private msg_id was not captured"
    u1.send(MessageType.MSG_RECALL, {"msg_id": private_msg_id})
    resp = u1.recv(MessageType.MSG_RECALL, predicate=lambda p: "success" in p)
    assert resp, "recall response missing"
    assert resp["payload"].get("success"), resp

    notify = u2.recv(
        MessageType.MSG_RECALL,
        predicate=lambda p: p.get("msg_id") == private_msg_id and p.get("recalled") is True,
    )
    assert notify, "receiver did not receive recall notification"


def t10_history():
    u1.send(
        MessageType.HISTORY_REQ,
        {"type": "private", "target_type": "private", "target_id": u2.user_id, "limit": 20},
    )
    resp = u1.recv(MessageType.HISTORY_RESP)
    assert resp, "history response missing"
    messages = resp["payload"].get("messages", [])
    assert any(m.get("msg_id") == private_msg_id for m in messages), messages


def t11_heartbeat():
    u1.send(MessageType.HEARTBEAT, {})
    resp = u1.recv(MessageType.HEARTBEAT_ACK)
    assert resp, "heartbeat ack missing"


def cleanup():
    u1.close()
    u2.close()


print("=" * 50)
print("End-to-end integration test")
print(f"user suffix: {SUFFIX}")
print("=" * 50)

try:
    test_step(1, "TCP connect", t1_connect)
    test_step(2, "register", t2_register)
    test_step(3, "login", t3_login)
    test_step(4, "private message delivery", t4_private_msg)
    test_step(5, "create group", t5_create_group)
    test_step(6, "join group", t6_join_group)
    test_step(7, "group message delivery", t7_group_msg)
    test_step(8, "online users", t8_online_users)
    test_step(9, "message recall by ACK UUID", t9_recall_msg)
    test_step(10, "history contains message", t10_history)
    test_step(11, "heartbeat", t11_heartbeat)
finally:
    cleanup()

print(f"\n{'=' * 50}")
if failed:
    print(f"Result: {passed}/{passed + failed} passed, {failed} failed")
else:
    print(f"Result: {passed}/{passed + failed} all passed")
print("=" * 50)

sys.exit(1 if failed else 0)
