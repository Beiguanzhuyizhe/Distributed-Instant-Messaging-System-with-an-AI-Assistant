# 安全与内容审核说明

本文档对应 Player3 负责的协议一致性、安全加密与内容审核部分，和当前代码实现保持一致。

## 1. 协议安全边界

- TCP 应用层协议统一使用 `Magic(2B) + Version(1B) + Type(1B) + Seq(4B) + PayloadLen(4B) + JSON Payload`。
- `server/protocol.py` 与 `client/protocol.py` 保持同一套 `MessageType`、`ErrorCode` 和 payload helper。
- `Seq` 用于请求/响应关联，`PayloadLen` 用于解决 TCP 粘包和半包问题。
- 服务端收到非法 `Magic` 或不完整 payload 时不会误解析为业务消息。

## 2. 端到端加密设计

客户端 `client/crypto.py` 实现 RSA-2048 + AES-256-GCM 混合加密：

1. 注册时生成 RSA 密钥对，公钥随 `REGISTER_REQ.public_key` 上传。
2. 发送私密内容时随机生成 AES-256 密钥。
3. 明文使用 AES-GCM 加密，AES 密钥使用接收方 RSA 公钥加密。
4. 密文 payload 使用 JSON 字符串保存，字段为 `aes_key_enc`、`nonce`、`ciphertext`、`tag`。
5. 服务端只存储和转发密文，不参与解密。

服务端 `server/crypto.py` 只负责公钥管理：存储、查询、批量查询和删除 `users.public_key`。

## 3. 内容审核策略

服务端 `server/content_moderator.py` 使用 Aho-Corasick 自动机进行 O(n) 多关键词匹配：

- `low`：无风险，直接放行。
- `mid`：辱骂、色情等中风险内容，替换敏感词后放行，并可发送警告。
- `high`：暴力威胁、政治敏感等高风险内容，直接拦截，并向发送者返回 `CONTENT_WARN`。

英文敏感词按大小写不敏感处理，例如 `fuck`、`FUCK`、`Fuck` 会被同一规则识别。

## 4. 与主聊天流程的关系

- 未加密的私聊和群聊内容会先经过内容审核，再写入历史记录和路由转发。
- 高风险内容不会进入聊天广播。
- 已加密内容服务端无法读取正文，应在文档中明确标记为 E2EE 扩展能力；若需要强审核，应使用未加密群聊或客户端侧审核。

## 5. 测试覆盖

- `tests/test_content_moderator.py`：覆盖正常消息、中风险替换、高风险拦截、英文大小写。
- `tests/test_crypto.py`：覆盖 RSA/AES 消息加解密、密文 JSON 字段、文件块 AES-GCM 加解密。
