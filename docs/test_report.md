# 最终测试报告

## 测试环境

| 项目 | 内容 |
|------|------|
| 测试日期 | 2026-06-11 |
| 操作系统 | Microsoft Windows 11 家庭版 中文版 |
| Python | 3.13.1 |
| 测试目录 | `final_work` |
| 服务端地址 | `127.0.0.1:8888` |

本报告只记录本机实际复现结果，不沿用旧报告中未复现的数据。

## 依赖安装

已执行：

```bash
python -m pip install -r requirements.txt
```

核心依赖包括 `aiohttp`、`cryptography`、`prompt_toolkit`、`rich`、`pytest`、`pytest-asyncio`。

## 单元测试

执行命令：

```bash
python -m pytest
```

结果：

```text
collected 87 items
87 passed in 2.87s
```

覆盖范围：

| 测试文件 | 重点 |
|----------|------|
| `tests/test_protocol.py` | 12 字节协议头、粘包/半包、消息类型、payload helper |
| `tests/test_database.py` | SQLite 初始化、约束、用户写入查询 |
| `tests/test_content_moderator.py` | 内容审核普通/中风险/高风险/英文大小写 |
| `tests/test_crypto.py` | RSA/AES 加解密 smoke test |
| `tests/test_client_player2.py` | 客户端 ACK、撤回、历史、文件 ID、CLI/GUI 一致性 |
| `tests/test_ai_service.py` | AI Key 选择、无 key、HTTP 错误、超时、解析失败 |
| `tests/test_file_transfer.py` | 文件 ID 校验、越权上传/下载、重复 chunk、完成状态 |

## 集成测试

启动服务端：

```bash
python -m server.main
```

执行命令：

```bash
python tests/run_integration_tests.py
```

结果：

```text
Result: 11/11 all passed
```

通过步骤：

1. TCP 连接
2. 用户注册
3. 用户登录
4. 私聊消息真实接收
5. 创建群组
6. 加入群组
7. 群聊消息真实接收
8. 在线用户列表
9. 使用服务端 ACK 返回的 UUID 撤回消息
10. 历史记录包含已发送消息
11. 心跳 ACK

## 压力测试

启动服务端后执行：

```bash
python tests/stress_test.py --clients 50 --concurrency 20 --messages 3
```

结果：

| 指标 | 数值 |
|------|------|
| Total Clients | 50 |
| Connected | 50 |
| Registered | 50 |
| Login Success | 50 |
| Messages Sent | 150 |
| Messages ACKed | 150 |
| Messages Received | 150 |
| Total Errors | 0 |
| Duration | 1.68s |
| Avg Latency | 0.1022s |
| P50 Latency | 0.0041s |
| P99 Latency | 1.5187s |
| Throughput | 89.28 acked msg/s |
| Result | PASS |

继续执行：

```bash
python tests/stress_test.py --clients 100 --concurrency 50 --messages 3
```

结果：

| 指标 | 数值 |
|------|------|
| Total Clients | 100 |
| Connected | 100 |
| Registered | 100 |
| Login Success | 100 |
| Messages Sent | 300 |
| Messages ACKed | 300 |
| Messages Received | 300 |
| Total Errors | 0 |
| Duration | 3.18s |
| Avg Latency | 0.1717s |
| P50 Latency | 0.0602s |
| P99 Latency | 2.7222s |
| Throughput | 94.33 acked msg/s |
| Result | PASS |

## AI 测试结论

AI 单元测试通过 mock HTTP 行为验证，不依赖真实 API Key：

- `BIGMODEL_API_KEY` 优先使用 BigModel 默认地址和模型
- 仅 `DASHSCOPE_API_KEY` 时使用 DashScope OpenAI-compatible 默认地址和 `qwen-turbo`
- `AI_API_BASE` / `AI_MODEL` 可覆盖默认值
- 无 key、401、超时、网络错误、解析失败均有友好兜底

真实外部 API 调用未在本次报告中验证，现场演示如需展示 AI 回复，应提前配置有效 key 并做一次 smoke test。

## 文件传输测试结论

服务端中继文件传输已补充以下校验并通过单元测试：

- `file_id` 仅允许安全 token/UUID 风格，阻止路径穿越
- `filename` 只保留 basename
- 上传 chunk 必须来自原 sender
- 下载 chunk 必须来自 receiver；群文件下载要求群成员身份
- `chunk_index`、`total_chunks`、单块大小和 offset 边界受校验
- 重复 chunk 不会虚增 `chunks_received`

## P2P 说明

P2P 打洞代码已保留并修正服务端地址交换调用，但没有在真实复杂 NAT 环境下验证。最终演示建议主打服务端中继文件传输，P2P 作为实验性扩展说明。

## 剩余风险

- GUI 多窗口手工演示仍建议现场前再做一次 smoke test。
- AI 真实接口依赖外部网络、账户额度和 API Key，有不确定性。
- P2P UDP 打洞受 NAT、防火墙、局域网策略影响，不作为必成演示路径。
