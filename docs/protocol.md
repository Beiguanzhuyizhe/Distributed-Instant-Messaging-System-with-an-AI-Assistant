# 通信协议文档 v1.0

## 1. 二进制消息格式

每条消息由一个 **12 字节的固定头部** 后跟 **变长 JSON Payload** 组成。

### 1.1 头部结构

```text
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|          Magic (0xCAFE)       |  Version  |    Type    |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                       Sequence Number                         |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                      Payload Length                           |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                                                               |
|                     Payload (JSON bytes)                      |
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

| 偏移 | 大小 | 字段 | struct 格式 | 说明 |
|------|------|------|-------------|------|
| 0 | 2B | Magic | `H` | 魔术字 `0xCAFE`，用于校验 |
| 2 | 1B | Version | `B` | 协议版本号，当前 `0x01` |
| 3 | 1B | Type | `B` | 消息类型（见类型表） |
| 4 | 4B | Seq | `I` | 序列号，大端序，用于请求-响应匹配 |
| 8 | 4B | PayloadLen | `I` | JSON payload 字节长度，大端序 |
| 12 | - | Payload | — | UTF-8 编码的 JSON 对象 |

**编码**: `struct.pack("!H B B I I", MAGIC, VERSION, msg_type, seq, payload_len) + json_payload`

### 1.2 消息类型表

| Code | 常量名 | 方向 | 说明 |
|------|--------|------|------|
| 0x01 | LOGIN_REQ | C -> S | 登录请求 |
| 0x02 | LOGIN_RESP | S -> C | 登录响应 |
| 0x03 | REGISTER_REQ | C -> S | 注册请求 |
| 0x04 | REGISTER_RESP | S -> C | 注册响应 |
| 0x05 | PRIVATE_MSG | C <-> S | 私聊消息 |
| 0x06 | GROUP_MSG | C <-> S | 群聊消息 |
| 0x07 | HEARTBEAT | C -> S | 心跳包 |
| 0x08 | HEARTBEAT_ACK | S -> C | 心跳确认 |
| 0x09 | FILE_INIT | C <-> S | 文件传输初始化 |
| 0x0A | FILE_DATA | C <-> S | 文件数据块 |
| 0x0B | FILE_ACK | C <-> S | 文件块确认（断点续传） |
| 0x0C | GROUP_CREATE | C <-> S | 创建群组 |
| 0x0D | GROUP_JOIN | C <-> S | 加入群组 |
| 0x0E | GROUP_LEAVE | C <-> S | 退出群组 |
| 0x0F | STATUS_UPDATE | S -> C | 在线状态推送 |
| 0x10 | MSG_RECALL | C <-> S | 消息撤回 |
| 0x11 | AI_QUERY | C -> S | @AI 查询 |
| 0x12 | AI_RESP | S -> C | AI 回复 |
| 0x13 | CONTENT_WARN | S -> C | 内容违规警告 |
| 0x14 | HISTORY_REQ | C -> S | 历史消息请求 |
| 0x15 | HISTORY_RESP | S -> C | 历史消息响应 |
| 0x16 | ONLINE_USERS | C <-> S | 在线用户列表 |
| 0x17 | P2P_HOLE_PUNCH | C <-> S | P2P 打洞协助 |
| 0x18 | P2P_READY | C <-> S | P2P 就绪通知 |
| 0xFF | ERROR | S -> C | 错误响应 |

### 1.3 错误码

| Code | 常量名 | 说明 |
|------|--------|------|
| 0 | SUCCESS | 成功 |
| 1 | INVALID_REQUEST | 无效请求 |
| 2 | AUTH_FAILED | 认证失败 |
| 3 | USER_EXISTS | 用户已存在 |
| 4 | USER_NOT_FOUND | 用户不存在 |
| 5 | GROUP_NOT_FOUND | 群组不存在 |
| 6 | NOT_GROUP_MEMBER | 非群组成员 |
| 7 | MESSAGE_TOO_LARGE | 消息过大 |
| 8 | FILE_TOO_LARGE | 文件过大 |
| 9 | RATE_LIMITED | 请求频率限制 |
| 10 | INTERNAL_ERROR | 服务端内部错误 |
| 11 | P2P_FAILED | P2P 连接失败 |
| 12 | MSG_NOT_FOUND | 消息未找到 |
| 13 | RECALL_TIMEOUT | 撤回超时（超过 2 分钟） |
| 14 | CONTENT_REJECTED | 内容被拒绝 |
| 15 | INVALID_PAYLOAD | 无效的 payload |

---

## 2. 交互流程

### 2.1 登录流程

```text
Client                          Server
  |                               |
  |--- LOGIN_REQ ---------------->|  {username, password_hash}
  |                               |  验证用户
  |<-- LOGIN_RESP -----------------|  {success, user_id, token}
```

### 2.2 注册流程

```text
Client                          Server
  |                               |
  |--- REGISTER_REQ ------------->|  {username, password_hash, public_key?}
  |                               |  创建用户
  |<-- REGISTER_RESP --------------|  {success, user_id}
```

### 2.3 私聊消息流程

```text
Client A                     Server                    Client B
  |                            |                          |
  |--- PRIVATE_MSG ----------->|                          |  {from_id, to_id, content, msg_id, timestamp}
  |                            |--- PRIVATE_MSG -------->|  转发消息
  |                            |                          |  (B 在线则实时推送)
```

### 2.4 群聊消息流程

```text
Client A                     Server                    Client B,C,...
  |                            |                          |
  |--- GROUP_MSG ------------->|                          |  {from_id, group_id, content, msg_id, timestamp}
  |                            |--- GROUP_MSG ---------->|  转发给所有群成员（除发送者）
```

### 2.5 心跳机制

```text
Client                          Server
  |                               |
  |--- HEARTBEAT ---------------->|  {}
  |<-- HEARTBEAT_ACK -------------|  {}
  |                               |
  (客户端每 25s 发送一次)
  (服务端 90s 无响应判定离线)
```

### 2.6 文件传输流程（中继模式）

```text
Client A                     Server                    Client B
  |                            |                          |
  |--- FILE_INIT ------------->|                          |  请求传文件
  |                            |--- FILE_INIT ---------->|  通知 B
  |                            |<-- FILE_ACK -------------|  B 确认接收
  |<-- FILE_ACK ---------------|                          |  分配 file_id
  |                            |                          |
  |--- FILE_DATA (chunk 1) --->|                          |  传输分块
  |                            |--- FILE_DATA (chunk 1) ->|  转发分块
  |<-- FILE_ACK ---------------|                          |  确认已接收
  |--- FILE_DATA (chunk N) --->|                          |  最后一块
  |                            |--- FILE_DATA (chunk N) ->|  chunk_index / total_chunks
```

### 2.7 消息撤回

```text
Client                      Server                    Receiver
  |                           |                          |
  |--- MSG_RECALL ----------->|  {msg_id, user_id}
  |                           |  检查是否在 2 分钟内
  |<-- MSG_RECALL ------------|  {success: true/false}
  |                           |--- STATUS_UPDATE ------>|  {type: "recall", msg_id}
```

### 2.8 P2P 打洞流程

```text
Client A                   Server                   Client B
  |                          |                         |
  |--- P2P_HOLE_PUNCH ------>|                         |  请求与 B 建立 P2P
  |                          |--- P2P_HOLE_PUNCH ----->|  包含 A 的地址
  |<-- P2P_HOLE_PUNCH -------|                         |  包含 B 的地址
  |                          |                         |
  |===== UDP 打洞阶段 =======|                         |
  |----- UDP 包 --->|                         |  双方互发 UDP 探测
  |                          |                         |
  |----- P2P_READY --------->|                         |  P2P 通道就绪
  |                          |--- P2P_READY ---------->|
```

---

## 3. Payload 格式定义

### LOGIN_REQ (0x01)
```json
{"username": "alice", "password_hash": "sha256hash..."}
```

### LOGIN_RESP (0x02) — 成功
```json
{"success": true, "user_id": 1, "token": "jwt-token..."}
```
### LOGIN_RESP (0x02) — 失败
```json
{"success": false, "code": 2, "message": "用户名或密码错误"}
```

### REGISTER_REQ (0x03)
```json
{"username": "bob", "password_hash": "sha256hash...", "public_key": "-----BEGIN PUBLIC KEY-----..."}
```

### REGISTER_RESP (0x04) — 成功
```json
{"success": true, "user_id": 2}
```

### PRIVATE_MSG (0x05)
```json
{
    "from_id": 1,
    "to_id": 2,
    "content": "你好！",
    "msg_id": 100,
    "timestamp": 1700000000
}
```

### GROUP_MSG (0x06)
```json
{
    "from_id": 1,
    "group_id": 1,
    "content": "大家好！",
    "msg_id": 101,
    "timestamp": 1700000001
}
```

### HEARTBEAT (0x07) / HEARTBEAT_ACK (0x08)
```json
{}
```

### FILE_INIT (0x09)
```json
{
    "from_id": 1,
    "to_id": 2,
    "filename": "doc.pdf",
    "filesize": 1024000,
    "file_id": "a1b2c3d4-uuid"
}
```

### FILE_DATA (0x0A)
```json
{
    "file_id": "a1b2c3d4-uuid",
    "chunk_index": 0,
    "total_chunks": 16,
    "data": "base64-encoded-chunk...",
}
```

### FILE_ACK (0x0B)
```json
{
    "file_id": "a1b2c3d4-uuid",
    "offset": 0
}
```
响应中包含 `data`（base64）、`offset`、`size`。服务端会校验下载者是否为接收方；群文件要求请求者是群成员。

### GROUP_CREATE (0x0C) — 请求
```json
{"user_id": 1, "name": "聊天群"}
```
### GROUP_CREATE (0x0C) — 响应
```json
{"group_id": 1, "success": true}
```

### GROUP_JOIN (0x0D)
```json
{"user_id": 1, "group_id": 1}
```
响应：`{"success": true}`

### GROUP_LEAVE (0x0E)
```json
{"user_id": 1, "group_id": 1}
```
响应：`{"success": true}`

### STATUS_UPDATE (0x0F)
```json
{"user_id": 1, "username": "alice", "is_online": 1}
```
`is_online` 取值：`1` 在线，`0` 离线。

### MSG_RECALL (0x10)
```json
{"msg_id": "6f1b1e8d-9c84-4e6f-9d8c-123456789abc"}
```
响应：`{"success": true, "msg_id": "...", "receiver_id": 2, "group_id": null}`

### AI_QUERY (0x11)
```json
{"group_id": 1, "user_id": 1, "from_id": 1, "query": "今天天气如何", "msg_id": 200}
```
字段说明：客户端实际发送 `query` 作为用户问题；`from_id` 与 `user_id` 兼容保留，服务端以登录态绑定的 `user_id` 为准。

### AI_RESP (0x12)
```json
{"group_id": 1, "user_id": 1, "from_id": 1, "query": "今天天气如何", "content": "我是AI助手...", "reply": "我是AI助手...", "msg_id": 201}
```
字段说明：`content` 是用于客户端显示的标准字段；`reply` 保留给旧版客户端兼容。

### CONTENT_WARN (0x13)
```json
{"user_id": 1, "msg_id": 300, "reason": "包含违规词汇", "message": "包含违规词汇", "level": "mid"}
```
`level` 取值：`low`、`mid`、`high`。`high` 表示服务端已拦截，`mid` 表示替换敏感词后可放行。

### HISTORY_REQ (0x14)
```json
{"user_id": 1, "target_type": "private", "target_id": 2, "limit": 50, "before_id": 0}
```
`target_type`: `private` / `group`

### HISTORY_RESP (0x15)
```json
{
    "messages": [
        {"id": 1, "msg_type": 5, "sender_id": 1, "target_id": 2, "content": "...", "created_at": "..."}
    ]
}
```

### ONLINE_USERS (0x16) — 请求
```json
{}
```
### ONLINE_USERS (0x16) — 响应
```json
{"users": [{"id": 1, "username": "alice", "public_key": ""}], "count": 1}
```

### P2P_HOLE_PUNCH (0x17)
```json
{"user_id": 1, "target_id": 2, "addr": "192.168.1.2:9000"}
```

### P2P_READY (0x18)
```json
{"user_id": 1, "target_id": 2, "addr": "192.168.1.2:9001"}
```

### ERROR (0xFF)
```json
{"code": 401, "message": "未授权"}
```

---

## 4. TCP 粘包处理

TCP 是流式协议，无消息边界。本项目采用 **长度前缀** 方式解决粘包/半包问题：

1. 发送端：在每个数据包前加上 12 字节的固定头，头中包含 `PayloadLength`
2. 接收端：先读取 12 字节头，解析出 payload_len，再读取 payload_len 字节的 Payload
3. 如果数据不足一个完整包，则等待更多数据

### MessageProtocol 示例

```python
from server.protocol import MessageProtocol, encode_message, MessageType

protocol = MessageProtocol()
protocol.feed(received_bytes)

for msg_type, seq, payload in protocol.next_messages():
    if msg_type == MessageType.PRIVATE_MSG:
        print(payload["content"])
```

### Connection 示例

```python
from server.protocol import Connection

async def handle_client(reader, writer):
    conn = Connection(reader, writer)
    await conn.send_message(MessageType.HEARTBEAT, {})
    msg_type, seq, payload = await conn.read_message()
```

---

## 5. 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| 服务端地址 | 0.0.0.0 | TCP 监听地址 |
| 服务端端口 | 8888 | TCP 监听端口 |
| 心跳间隔（客户端） | 25s | 略短于服务端超时 |
| 心跳超时（服务端） | 90s | 超时则断开连接 |
| 缓冲区大小 | 4096 | 网络 I/O 缓冲区 |
| 文件块大小 | 64KB | 文件传输分块大小 |
| 最大文件大小 | 100MB | 单文件上限 |
| 撤回窗口 | 120s | 消息可撤回时间 |
| 最大负载 | 1MB | 单消息最大 payload |

---

## 6. 数据库 Schema

### users
| 列 | 类型 | 约束 |
|----|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| username | TEXT | UNIQUE NOT NULL |
| password_hash | TEXT | NOT NULL |
| public_key | TEXT | NULLABLE（RSA 公钥 PEM） |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP |

### groups
| 列 | 类型 | 约束 |
|----|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| name | TEXT | NOT NULL |
| owner_id | INTEGER | NOT NULL, FK -> users(id) |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP |

### group_members
| 列 | 类型 | 约束 |
|----|------|------|
| group_id | INTEGER | NOT NULL, FK -> groups(id) |
| user_id | INTEGER | NOT NULL, FK -> users(id) |
| PRIMARY KEY | (group_id, user_id) |

### messages
| 列 | 类型 | 约束 |
|----|------|------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| msg_type | INTEGER | NOT NULL |
| sender_id | INTEGER | NOT NULL, FK -> users(id) |
| target_id | INTEGER | NOT NULL（用户ID 或 群组ID） |
| content | TEXT | NULLABLE |
| file_path | TEXT | NULLABLE |
| file_size | INTEGER | NULLABLE |
| is_recalled | INTEGER | DEFAULT 0 |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP |

---

## 7. 客户端本地存储

客户端使用 JSON 文件存储消息历史，按会话分文件：

```text
message_store/
├── private_<my_id>_<peer_id>.json   # 私聊记录
├── group_<group_id>.json             # 群聊记录
└── index.json                        # 会话索引
```

单条消息格式：
```json
{
    "msg_id": 123,
    "msg_type": 5,
    "sender_id": 1,
    "from_me": true,
    "content": "你好",
    "is_recalled": false,
    "timestamp": 1700000000,
    "file_path": null,
    "file_size": null
}
```

---

## 8. 变更记录

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v1.0 | 2026-04-30 | 初始版本，定义完整协议（12B 头 + JSON payload） |
