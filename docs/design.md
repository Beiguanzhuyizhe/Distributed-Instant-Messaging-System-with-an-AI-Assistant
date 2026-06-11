# 分布式即时聊天系统 —— 设计文档

## 1. 系统架构概览

```text
+------------------------------------------------------------------+
|                         Client (TCP)                              |
|  +----------+  +--------+  +-------+  +--------+  +-----------+  |
|  |  GUI     |  |  CLI   |  | Crypto|  |  P2P   |  | MsgStore  |  |
|  | (tkinter)|  | (rich) |  |(E2EE) |  |(UDP)   |  | (JSON)    |  |
|  +----+-----+  +----+---+  +---+---+  +---+----+  +-----+-----+  |
|       |             |           |           |            |        |
|       +------+------+-----------+-----------+------------+        |
|              |                                                     |
|         +----+------+                                              |
|         | MsgHandler|    (消息分发/路由)                            |
|         +----+------+                                              |
|              |                                                     |
|         +----+------+                                              |
|         | Connection|    (TCP socket + MessageProtocol)            |
|         +----+------+                                              |
|              |                                                     |
+--------------+-----------------------------------------------------+
               |  TCP (Binary Protocol: 12B Header + JSON Payload)
               |
+--------------+-----------------------------------------------------+
|                      Server (asyncio)                              |
|         +----+------+                                              |
|         | Connection|    (asyncio + MessageProtocol)               |
|         +----+------+                                              |
|              |                                                     |
|         +----+---------+                                           |
|         | ConnectionManager                                        |
|         | (conn_id<->user_id 映射)                                  |
|         +----+---------+                                           |
|              |                                                     |
|    +---------+-----------+-----------+-----------+                 |
|    |         |           |           |           |                 |
| +--+---+ +--+---+ +-----+-----+ +--+------+ +--+------+           |
| |Auth  | | Msg  | | Group     | | File    | | Msg     |           |
| |Login | |Router| | Manager   | |Transfer | | History |           |
| |Reg   | |      | |           | |(Relay)  | | Recall  |           |
| +------+ +------+ +-----------+ +---------+ +---------+           |
|    |         |           |           |           |                 |
|    +---------+-----------+-----------+-----------+                 |
|              |                                                     |
|    +---------+---------+                                           |
|    | HeartbeatMonitor  |  (超时清理)                                |
|    +---------+---------+                                           |
|              |                                                     |
|    +---------+---------+-------+---------+                         |
|    |                   |       |         |                         |
| +--+---+     +--------+-+  +--+---+  +--+------+                  |
| | AI   |     | Content |  | P2P  |  | Key     |                  |
| |Service|    |Moderate |  |Helper|  |Manager  |                  |
| |(BigModel)| |(AC自动机)|  |      |  |(公钥)   |                  |
| +------+     +---------+  +------+  +---------+                  |
|              |                                                     |
|         +----+--------+                                            |
|         |   Database    | (SQLite, 6 tables)                       |
|         +-------------+                                            |
+--------------------------------------------------------------------+
```

---

## 2. 服务器架构

### 2.1 模块清单

| 模块 | 文件 | 职责 |
|------|------|------|
| TCP Server Core | `server/tcp_server.py` | asyncio 事件循环、连接管理、消息派发 |
| Protocol | `server/protocol.py` | 二进制协议编解码、粘包处理、Connection 封装 |
| Config | `server/config.py` | 配置项（网络、心跳、AI、文件、DB） |
| Database | `server/database.py` | SQLite 存储：用户/群组/消息/文件传输 |
| User Manager | `server/user_manager.py` | 用户注册、登录、在线状态管理 |
| Group Manager | `server/group_manager.py` | 群组创建、加入、退出、成员查询 |
| Message Router | `server/message_router.py` | 私聊/群聊消息存储与转发、在线状态广播 |
| Message History | `server/message_history.py` | 历史消息查询、分页 |
| Message Recall | `server/message_recall.py` | 消息撤回（2 分钟窗口） |
| Heartbeat | `server/heartbeat.py` | 心跳超时检测、自动清理离线连接 |
| File Transfer | `server/file_transfer.py` | 文件中继存储与分块传输 |
| AI Service | `server/ai_service.py` | @AI 智能回复（默认智谱 BigModel，兼容 DashScope/OpenAI 格式接口） |
| Content Moderator | `server/content_moderator.py` | 内容审核（Aho-Corasick 关键词匹配） |
| P2P Helper | `server/p2p_helper.py` | P2P 打洞协助、地址交换 |
| Key Manager | `server/crypto.py` | 用户 RSA 公钥存储与查询 |

### 2.2 核心流程：连接生命周期

```text
建立连接 → 认证 → 消息收发 → 心跳维持 → 断开清理
  |          |         |          |          |
  |   LOGIN_REQ   PRIVATE_MSG  HEARTBEAT  移除映射
  |   LOGIN_RESP  GROUP_MSG    HEARTBEAT  广播离线
  |   REGISTER    FILE_INIT    ACK        关闭 socket
  |               MSG_RECALL
```

### 2.3 TCP Server 核心

`ChatServer` 使用 `asyncio.start_server` 实现高并发：
- 每个客户端连接一个协程 (`_handle_client`)
- `ConnectionManager` 维护 `conn_id -> Connection` 和 `user_id -> conn_id` 两套映射
- `MessageRouter` 负责业务分发：私聊存储后推送、群聊存储后广播、在线状态全网广播

**ConnectionManager 关键特性**：
- 互斥锁保护映射表（asyncio.Lock）
- `bind_user()` 同一用户重复登录时主动踢掉旧连接
- `send_to_user()` 按 user_id 精确推送
- `broadcast()` 广播（可选排除发送者）

---

## 3. 客户端架构

### 3.1 模块清单

| 模块 | 文件 | 职责 |
|------|------|------|
| Entry | `client/main.py` | CLI 参数解析、选择 GUI/CLI 模式 |
| Connection | `client/connection.py` | 阻塞 socket + 后台线程，心跳/重连 |
| Protocol | `client/protocol.py` | 与服务端一致的协议编解码 |
| Message Handler | `client/message_handler.py` | 消息分发 + 快捷发送方法 |
| GUI | `client/gui.py` | tkinter 图形界面 |
| CLI | `client/cli.py` | rich 命令行界面 |
| Message Store | `client/message_store.py` | JSON 本地消息持久化 |
| Crypto | `client/crypto.py` | RSA-2048 + AES-256-GCM 端到端加密 |
| P2P | `client/p2p.py` | UDP 打洞直连 + P2P 文件传输 |

### 3.2 连接管理

`ChatConnection` 使用阻塞 socket + 后台接收线程模式：
- 主线程不阻塞，适合 GUI/CLI
- 启动独立线程运行接收循环
- 自动心跳（15 秒间隔）
- 指数退避自动重连（最多 5 次）

### 3.3 消息分发

`MessageHandler` 自动将 Connection 收到的所有消息路由到应用层注册的回调：
- `register(msg_type, callback)` — 按消息类型注册回调
- 应用层不需要关心底层协议细节

---

## 4. 通信协议

详见 `docs/protocol.md`。

**协议要点**：
- **传输层**：TCP，二进制协议
- **消息头**：12 字节固定头 `!H B B I I`
  - Magic(2B) = 0xCAFE, Version(1B) = 0x01, Type(1B), Seq(4B), PayloadLen(4B)
- **消息体**：JSON 编码的 payload（UTF-8）
- **粘包处理**：通过 `PayloadLen` 字段 + `MessageProtocol` 内部 buffer 解决
- **消息类型**：26 种，覆盖认证、消息、文件、群组、AI、P2P
- **错误码**：16 种（SUCCESS=0 ~ INVALID_PAYLOAD=15）

---

## 5. AI 智能回复设计

### 5.1 基本原理

当用户在群聊中 `@AI` 时，服务端截获消息内容，调用大模型 API 生成回复。

### 5.2 架构

```text
客户端 @AI 发送 AI_QUERY
    → 服务端 tcp_server 处理 AI_QUERY
    → 调用 AIService.query(prompt)
    → aiohttp 异步请求智谱 BigModel API（非阻塞）
    → 获取回复后以 AI_RESP 类型发回群聊
    → 所有群成员收到 AI 回复
```

### 5.3 关键技术

- **异步 HTTP**：使用 `aiohttp` 非阻塞调用，不阻塞服务端事件循环
- **流式响应**：支持 SSE 流式输出（实时显示 AI 打字效果）
- **对话上下文**：携带最近 N 条群聊消息作为上下文
- **提示词设计**：系统提示词定义 AI 为"友好、乐于助人的聊天助手"
- **API 兼容**：默认 BigModel（智谱清言），也可通过 `AI_API_BASE` 切换 DashScope（通义千问）等 OpenAI 兼容接口

### 5.4 API 配置

```python
AI_API_BASE = "https://open.bigmodel.cn/api/paas/v4"
AI_API_KEY = os.getenv("BIGMODEL_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
AI_MODEL = "glm-4-flash-250414"
```

---

## 6. P2P 打洞设计

### 6.1 基本原理

NAT 环境下两个内网客户端无法直接互通。通过服务端协助交换地址信息，双方同时向对方地址发送 UDP 包，在 NAT 上建立出站映射，使入站包能够穿透。

### 6.2 完整流程

```text
1. A 请求向 B 传文件
2. A 通过 TCP 向服务端发送 P2P_HOLE_PUNCH {target_id: B}
3. 服务端 P2PHelper 查询 B 的地址（TCP 地址 / UDP 地址）
4. 服务端将 A 的地址发给 B，将 B 的地址发给 A
5. A 开始向 B 的地址发送 UDP PUNCH 包
6. B 同时向 A 的地址发送 UDP PUNCH 包
7. NAT 建立映射，双方可直连
8. 连接建立后，通过 P2P 加密通道传输文件分块
```

### 6.3 P2P 协议（UDP 层）

UDP 数据包也使用二进制头，但与 TCP 协议独立：

```text
Magic(2B)=0x504E + Version(1B) + Type(1B) + Seq(8B) + PayloadLen(4B) = 16B
```

P2P 消息类型：
| 类型 | 值 | 说明 |
|------|-----|------|
| P2P_FILE_INIT | 0x01 | 文件传输初始化 |
| P2P_FILE_DATA | 0x02 | 文件数据块 |
| P2P_FILE_ACK | 0x03 | 接收确认 |
| P2P_FILE_RESUME | 0x04 | 断点续传请求 |
| P2P_PUNCH | 0x05 | 打洞探测包 |
| P2P_PUNCH_ACK | 0x06 | 打洞确认 |

### 6.4 断点续传

- 文件分块（默认 64KB），每块独立传输
- 接收方记录已接收的 offset，发送方据此跳过已传输部分
- P2P_FILE_RESUME 请求可指定起始 offset，实现续传

---

## 7. 端到端加密 (E2EE) 设计

### 7.1 加密方案

采用 **RSA-2048 + AES-256-GCM 混合加密**：

```text
发送方:
  AES密钥 = random(32 bytes)          # 随机生成 AES-256 密钥
  密文 = AES-GCM-Encrypt(明文, AES密钥) # 用 AES 加密消息内容
  加密的AES密钥 = RSA-Encrypt(AES密钥, 接收方公钥)  # RSA 加密 AES 密钥
  发送: {aes_key_enc, nonce, ciphertext, tag}

接收方:
  AES密钥 = RSA-Decrypt(aes_key_enc, 自己的私钥)
  明文 = AES-GCM-Decrypt(ciphertext + tag, AES密钥, nonce)
```

### 7.2 密钥生命周期

1. **注册时**：客户端生成 RSA-2048 密钥对
2. **公钥上传**：公钥 (PEM) 在注册请求中发送给服务端，存入 `users.public_key`
3. **私钥本地保存**：私钥保存在客户端本地文件，密码保护
4. **消息加密**：发送时用接收方公钥加密，只有接收方私钥能解密

### 7.3 加密传输格式

```json
{
    "from_id": 1,
    "to_id": 2,
    "content": "{\"aes_key_enc\":\"base64...\",\"nonce\":\"base64...\",\"ciphertext\":\"base64...\",\"tag\":\"base64...\"}",
    "msg_id": 100,
    "timestamp": 1700000000
}
```

- 服务端无法解密加密后的内容
- 加密消息仍经过服务端中继转发
- `content` 内部 JSON 字段与 `client/crypto.py` 保持一致：`aes_key_enc`、`nonce`、`ciphertext`、`tag`

### 7.4 文件加密

P2P 直连文件传输使用 AES-256-GCM 加密：
- 每个文件生成随机 AES 密钥
- AES 密钥使用接收方 RSA 公钥加密后，通过 TCP 通道发送
- 文件数据通过 P2P UDP 通道加密传输

---

## 8. 内容审核设计

### 8.1 架构

使用 **Aho-Corasick 自动机** 实现 O(n) 多模式匹配：

- 在服务端 `content_moderator.py` 中实现
- 支持关键词分类管理（辱骂/暴力/色情/政治敏感）
- 建立 fail 指针的 BFS 预处理，匹配时只需遍历一次文本

### 8.2 处理流程

```text
消息到达 → 内容审核 → 通过 → 转发
                    → high 拒绝 → 发送 CONTENT_WARN 给发送者
                    → mid 替换敏感词 → 继续转发并可提示
```

- 仅审核未加密消息，英文敏感词大小写不敏感
- 加密消息跳过审核，但标记为"加密消息"

---

## 9. 数据库设计

### 9.1 表结构

6 张表，详见 `server/database.py`：

| 表名 | 用途 | 关键字段 |
|------|------|---------|
| users | 用户账户 | id, username, password_hash, public_key |
| groups | 群组 | id, name, owner_id |
| group_members | 群组成员关系 | group_id, user_id |
| messages | 消息存储 | id, sender_id, receiver_id, group_id, content |
| offline_messages | 离线消息 | target_user_id, content, delivered |
| file_transfers | 文件传输记录 | file_id, sender_id, filename, status, chunks |

### 9.2 关键索引

```sql
idx_messages_sender, idx_messages_receiver, idx_messages_group
idx_messages_created, idx_offline_target
```

---

## 10. 配置设计

### 10.1 服务端配置 (`server/config.py`)

| 配置项 | 默认值 | 环境变量 |
|--------|--------|---------|
| host | 0.0.0.0 | - |
| tcp_port | 8888 | CHAT_PORT |
| heartbeat_timeout | 90s | - |
| ai_api_key | "" | BIGMODEL_API_KEY / DASHSCOPE_API_KEY |
| file_chunk_size | 64KB | - |
| max_file_size | 100MB | - |
| recall_window | 120s | - |

### 10.2 客户端配置 (`client/config.py`)

| 配置项 | 默认值 | 环境变量 |
|--------|--------|---------|
| server_host | 127.0.0.1 | CHAT_SERVER_HOST |
| server_port | 8888 | CHAT_SERVER_PORT |
| heartbeat_interval | 25s | - |
| reconnect_delay_min | 1s | - |
| reconnect_delay_max | 30s | - |

---

## 11. 安全设计

| 层面 | 措施 | 说明 |
|------|------|------|
| 传输 | E2EE 加密 | RSA-2048 + AES-256-GCM，服务端无法解密 |
| 文件 | P2P 直连加密 | AES-256-GCM 加密文件数据 |
| 认证 | 密码哈希 | 服务端不存明文密码 |
| 审核 | 关键词过滤 | Aho-Corasick 自动机实时审核 |
| 连接 | Token 验证 | 登录后服务端返回 token（预留扩展） |

---

## 12. 可靠性设计

| 场景 | 处理方式 |
|------|---------|
| 服务端宕机 | 客户端自动重连（指数退避） |
| 网络闪断 | TCP 连接断开后心跳超时清理 |
| 消息未送达 | 离线消息存储，上线后推送 |
| 大文件传输 | 分块传输 + 断点续传 + FILE_ACK 确认 |

---

## 13. 快速启动

### 服务端
```bash
export BIGMODEL_API_KEY="your-api-key"  # AI 功能可选
python -m server.main
```

### 客户端
```bash
# CLI 模式（默认）
python -m client.main

# GUI 模式
python -m client.main --gui
```

---

## 14. 参考文档

- [通信协议文档](protocol.md) — 完整的消息格式定义
- `server/protocol.py` — 协议编解码实现
- `server/database.py` — 数据库 Schema 定义
