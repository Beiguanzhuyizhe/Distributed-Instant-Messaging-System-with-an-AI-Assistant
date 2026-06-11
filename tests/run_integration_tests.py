"""
端到端集成测试 - 验证核心业务流程

使用方式:
  1. 先启动服务器: python server/main.py
  2. 运行本测试: python tests/run_integration_tests.py

注意:
  - 每次运行使用独立用户名（时间戳后缀），避免与服务器残留数据冲突
  - 本文件命名不以 test_ 开头，不会干扰 pytest 自动收集
"""
import socket
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server.protocol import (
    MessageType,
    encode_message,
    decode_messages,
)

HOST = '127.0.0.1'
PORT = 8888
TIMEOUT = 5
SUFFIX = str(int(time.time()))[-6:]  # 时间戳后缀，避免用户名冲突


class Client:
    def __init__(self, name):
        self.name = name
        self.sock = None
        self.user_id = None

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(TIMEOUT)
        self.sock.connect((HOST, PORT))

    def send(self, msg_type, payload):
        data = encode_message(msg_type, payload)
        self.sock.sendall(data)

    def recv(self, expected_type=None, timeout=TIMEOUT):
        self.sock.settimeout(timeout)
        buf = b''
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                return None
            if not chunk:
                return None
            buf += chunk
            msgs, remaining = decode_messages(buf)
            if msgs:
                buf = remaining
                for msg in msgs:
                    if expected_type is None or msg[0] == expected_type:
                        return {"type": msg[0], "payload": msg[2]}
        return None

    def drain(self):
        self.sock.settimeout(0.3)
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
        except socket.timeout:
            pass
        self.sock.settimeout(TIMEOUT)

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None


# ====== 测试基础设施 ======

passed = 0
failed = 0


def test_step(n, desc, func):
    global passed, failed
    print(f"\n--- 测试 {n}: {desc} ---")
    try:
        func()
        print(f"  [PASS] 通过")
        passed += 1
    except AssertionError as e:
        print(f"  [FAIL] 断言失败: {e}")
        failed += 1
    except Exception as e:
        print(f"  [FAIL] 异常: {type(e).__name__}: {e}")
        failed += 1


# ====== 测试流程 ======

u1 = Client("用户1")
u2 = Client("用户2")
U1_NAME = f"alice_{SUFFIX}"
U2_NAME = f"bob_{SUFFIX}"
group_id = None


def t1_connect():
    u1.connect()
    u2.connect()


def t2_register():
    u1.send(MessageType.REGISTER_REQ, {"username": U1_NAME, "password_hash": "pass123"})
    resp = u1.recv(MessageType.REGISTER_RESP)
    assert resp, "无注册响应"
    assert resp['payload'].get('success'), f"注册 u1 失败: {resp}"
    u1.user_id = resp['payload'].get('user_id')
    assert u1.user_id is not None, "未返回 user_id"
    print(f"  u1 user_id={u1.user_id}")

    u2.send(MessageType.REGISTER_REQ, {"username": U2_NAME, "password_hash": "pass456"})
    resp = u2.recv(MessageType.REGISTER_RESP)
    assert resp, "无注册响应"
    assert resp['payload'].get('success'), f"注册 u2 失败: {resp}"
    u2.user_id = resp['payload'].get('user_id')
    assert u2.user_id is not None, "未返回 user_id"
    print(f"  u2 user_id={u2.user_id}")

    assert u1.user_id != u2.user_id, "两个用户 user_id 应不同"


def t3_login():
    u1.send(MessageType.LOGIN_REQ, {"username": U1_NAME, "password_hash": "pass123"})
    resp = u1.recv(MessageType.LOGIN_RESP)
    assert resp, "无登录响应"
    assert resp['payload'].get('success'), f"u1 登录失败: {resp}"

    u2.send(MessageType.LOGIN_REQ, {"username": U2_NAME, "password_hash": "pass456"})
    resp = u2.recv(MessageType.LOGIN_RESP)
    assert resp, "无登录响应"
    assert resp['payload'].get('success'), f"u2 登录失败: {resp}"


def t4_private_msg():
    u1.drain()
    u1.send(MessageType.PRIVATE_MSG, {"to_id": u2.user_id, "content": "Hello Bob!"})
    time.sleep(0.3)


def t5_create_group():
    global group_id
    u1.send(MessageType.GROUP_CREATE, {"group_name": f"TestGroup_{SUFFIX}"})
    resp = u1.recv(MessageType.GROUP_CREATE)
    assert resp, "无建群响应"
    assert resp['payload'].get('success'), f"建群失败: {resp}"
    group_id = resp['payload'].get('group_id')
    assert group_id is not None, "未返回 group_id"
    print(f"  group_id={group_id}")


def t6_join_group():
    assert group_id is not None, "group_id 未设置，t5 可能未执行"
    u2.send(MessageType.GROUP_JOIN, {"group_id": group_id})
    resp = u2.recv(MessageType.GROUP_JOIN)
    assert resp, "无加群响应"
    assert resp['payload'].get('success'), f"加群失败: {resp}"


def t7_group_msg():
    assert group_id is not None, "group_id 未设置"
    u2.drain()
    u2.send(MessageType.GROUP_MSG, {"group_id": group_id, "content": "Hello everyone!"})
    time.sleep(0.3)


def t8_online_users():
    u1.send(MessageType.ONLINE_USERS, {})
    resp = u1.recv(MessageType.ONLINE_USERS)
    assert resp, "获取在线用户无响应"
    users = resp['payload'].get('users', [])
    print(f"  在线用户: {users}")
    assert len(users) >= 2, f"应至少2人在线，当前: {len(users)}"
    usernames = [u['username'] for u in users]
    assert U1_NAME in usernames, f"u1 不在在线列表"
    assert U2_NAME in usernames, f"u2 不在在线列表"


def t9_recall_msg():
    u1.send(MessageType.MSG_RECALL, {"msg_id": "1"})
    time.sleep(0.3)


def t10_history():
    assert u2.user_id is not None, "u2 user_id 未设置"
    u1.send(MessageType.HISTORY_REQ, {"target_id": u2.user_id, "msg_type": 1})
    resp = u1.recv(MessageType.HISTORY_RESP)
    assert resp is not None, "获取历史消息无响应"


def t11_heartbeat():
    u1.send(MessageType.HEARTBEAT, {})
    resp = u1.recv(MessageType.HEARTBEAT_ACK)
    assert resp, "心跳无响应"
    assert resp['type'] == MessageType.HEARTBEAT_ACK, f"心跳响应类型错误: {resp}"


def cleanup():
    u1.close()
    u2.close()


# ====== 执行 ======

print("=" * 50)
print("端到端集成测试")
print(f"用户名后缀: {SUFFIX}")
print("=" * 50)

test_step(1, "TCP 连接", t1_connect)
test_step(2, "用户注册", t2_register)
test_step(3, "用户登录", t3_login)
test_step(4, "私聊消息", t4_private_msg)
test_step(5, "创建群组", t5_create_group)
test_step(6, "加入群组", t6_join_group)
test_step(7, "群聊消息", t7_group_msg)
test_step(8, "在线用户列表", t8_online_users)
test_step(9, "消息撤回", t9_recall_msg)
test_step(10, "历史消息查询", t10_history)
test_step(11, "心跳检测", t11_heartbeat)

print(f"\n{'=' * 50}")
if failed > 0:
    print(f"结果: {passed}/{passed + failed} 通过, {failed} 失败")
else:
    print(f"结果: {passed}/{passed + failed} 全部通过!")
print("=" * 50)

cleanup()
sys.exit(1 if failed > 0 else 0)
