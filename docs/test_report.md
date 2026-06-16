# 最终测试报告

## 测试环境

| 项目 | 内容 |
|------|------|
| 测试日期 | 2026-06-14 |
| 操作系统 | Microsoft Windows 11 家庭版 中文版 |
| Python | 3.13.5 |
| 测试目录 | `D:\Courses Learning\Computer Network\final_work` |
| 服务端地址 | `127.0.0.1:8888` |
| 主要测试目标 | 注册/登录、私聊、群聊、在线用户、历史、撤回、心跳、文件传输、内容审核、AI、并发压力 |

本报告只记录本次实际复现结果。真实 AI smoke test 使用用户级环境变量中的 `DEEPSEEK_API_KEY`，测试进程内临时映射为项目现有 OpenAI-compatible 配置变量；测试报告、源码、日志和文档均不记录 API Key 原文。

## 依赖安装

已执行：

```bash
python -m pip install -r requirements.txt
python -m pip install pytest==8.3.4 pytest-asyncio==0.23.8
```

补充说明：

- 初次完整运行 `python -m pytest` 时缺少 `pywebview` 和 `pytest-asyncio`，导致 GUI 相关测试无法收集；安装 `requirements.txt` 后解决依赖缺失。
- 安装依赖时曾短暂安装到 `pytest 9.1.0`，该版本在本 Windows 环境中触发临时目录清理 `PermissionError`。已恢复到 `pytest 8.3.4`，这是项目此前报告中使用过且更稳定的版本。
- `pip show pytest pytest-asyncio pywebview` 在 GBK 控制台下输出第三方包作者信息时出现 Unicode 日志编码错误，不影响已安装包本身。

## 单元与模块测试

### 可直接通过的 pytest 用例

执行命令：

```bash
python -m pytest tests\test_ai_service.py tests\test_content_moderator.py tests\test_crypto.py tests\test_database.py tests\test_protocol.py tests\test_client_player2.py -k "not send_file"
```

结果：

```text
78 passed, 1 deselected in 3.48s
```

覆盖范围：

| 测试文件 | 重点 |
|----------|------|
| `tests/test_protocol.py` | 12 字节协议头、粘包/半包、消息类型、payload helper |
| `tests/test_database.py` | SQLite 初始化、约束、用户写入查询 |
| `tests/test_content_moderator.py` | 内容审核普通/中风险/高风险/英文大小写 |
| `tests/test_crypto.py` | RSA/AES 加解密 smoke test |
| `tests/test_client_player2.py` | 客户端 ACK、撤回、历史、CLI/GUI 一致性 |
| `tests/test_ai_service.py` | AI Key 选择、无 key、HTTP 错误、超时、解析失败 |

### pytest 临时目录异常

完整命令：

```bash
python -m pytest tests --basetemp <系统临时目录>
```

在本机执行时，`tmp_path` fixture 创建/清理临时目录阶段出现：

```text
PermissionError: [WinError 5] 拒绝访问
```

受影响用例集中在：

- `tests/test_file_transfer.py`
- `tests/test_client_player2.py::test_cli_send_file_uses_string_file_id`

这些错误发生在 pytest 临时目录处理阶段，测试体未真正进入业务断言。为避免把环境问题误判为项目功能失败，后续使用直接验证脚本复现同等业务路径。

### 文件传输直接验证

由于 pytest 的 `tmp_path` fixture 在当前 Windows 环境中被临时目录权限问题阻断，本次使用临时验证脚本直接调用与 `tests/test_file_transfer.py` 和 `tests/test_client_player2.py::test_cli_send_file_uses_string_file_id` 等价的业务路径。临时脚本仅用于本次测试，未作为交付文件保留。

结果：

```text
rejects unsafe file_id: PASS
sanitizes filename: PASS
requires original sender: PASS
duplicate chunk idempotent: PASS
download requires receiver: PASS
group download members only: PASS
rejects bad chunk bounds: PASS
rejects invalid total_chunks: PASS
result: PASS

cli send file uses string file_id: PASS
calls: 2
```

验证结论：

- `file_id` 仅允许安全 token/UUID 风格，能阻止路径穿越。
- `filename` 会被归一化为 basename。
- 上传 chunk 必须来自原 sender。
- 私聊文件只能由 receiver 下载；群文件只能由群成员下载。
- `chunk_index`、`total_chunks`、单块大小和 offset 边界受校验。
- 重复 chunk 不会虚增进度。
- CLI 发送文件时使用字符串 `file_id`，并能发出初始化请求和数据块。

## 运行数据路径异常

本次测试发现当前工作区所在 D 盘路径下，SQLite 文件建表会触发 `sqlite3.OperationalError: disk I/O error`。复现范围包括：

- 默认运行数据库：`server/data/chat.db` 当前为 0 字节，并存在 `chat.db-journal`。
- 工作区临时数据库：`tmp/runtime/*.db`。

最小复现：

```bash
python -c "import sqlite3, pathlib; p=pathlib.Path('tmp/runtime/sqlite_now.db'); p.parent.mkdir(parents=True, exist_ok=True); con=sqlite3.connect(p); con.execute('create table if not exists t(x)'); con.commit()"
```

结果：

```text
sqlite3.OperationalError: disk I/O error
```

同一 Python 环境在系统临时目录 `C:\Users\yeyiwen\AppData\Local\Temp` 下可以正常创建 SQLite 数据库。因此，本次服务端集成测试、压力测试和 AI smoke test 使用系统临时目录中的隔离数据库：

```text
C:\Users\yeyiwen\AppData\Local\Temp\codex_chat_stress_runtime\chat.db
```

这个问题不影响 TCP 协议、消息路由、并发和 AI 逻辑的测试结论，但会影响默认方式 `python -m server.main` 在当前工作区直接启动。演示前建议清理或修复默认运行数据目录，见“改进建议”。

## 集成测试

测试服务端由临时 orchestration 脚本启动，配置仍使用项目的 `ChatServer`、`ServerConfig` 和业务模块，只把数据库与文件存储目录放到系统临时目录以避开当前工作区 SQLite I/O 异常。临时 orchestration 脚本负责启动服务端、等待端口就绪、运行测试命令并在 `finally` 中关闭服务端，未作为交付文件保留。

其中集成测试子步骤执行：

```bash
python tests/run_integration_tests.py
```

结果：

```text
Result: 11/11 all passed
```

通过步骤：

1. TCP 连接。
2. 用户注册。
3. 用户登录。
4. 私聊消息真实接收。
5. 创建群组。
6. 加入群组。
7. 群聊消息真实接收。
8. 在线用户列表。
9. 使用服务端 ACK 返回的 UUID 撤回消息。
10. 历史记录包含已发送消息。
11. 心跳 ACK。

## 压力测试

压力测试脚本测试的是大量虚拟客户端并发连接、注册、登录、发送私聊消息并等待 ACK。当前脚本中的每个虚拟客户端向自己发送私聊，因此能够验证连接数、认证、消息入库/ACK 和协议收发压力，但不等价于复杂多人互发或大规模群聊广播压力。

### 压力测试汇总

| 场景 | Connected | Login Success | Messages ACKed | Total Errors | Result |
|------|-----------|---------------|----------------|--------------|--------|
| 50 客户端并发 | 50/50 | 50/50 | 150/150 | 0 | PASS |
| 100 客户端并发 | 100/100 | 100/100 | 300/300 | 0 | PASS |
| 150 客户端并发 | 150/150 | 150/150 | 450/450 | 0 | PASS |
| 100 客户端高消息量 | 100/100 | 100/100 | 1000/1000 | 0 | PASS |
| 200 客户端连接上限 | 200/200 | 200/200 | 600/600 | 0 | PASS |

其中“100 客户端并发”对应作业要求中的“100 用户同时在线”展示点：测试中 100 个虚拟客户端均成功连接、注册、登录并完成消息 ACK。

### 50 客户端并发

命令：

```bash
python tests/stress_test.py --clients 50 --concurrency 20 --messages 3 --timeout 10
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
| Duration | 1.06s |
| Avg Latency | 0.0590s |
| P50 Latency | 0.0081s |
| P99 Latency | 0.8878s |
| Throughput | 141.41 acked msg/s |
| Result | PASS |

### 100 客户端并发

命令：

```bash
python tests/stress_test.py --clients 100 --concurrency 50 --messages 3 --timeout 10
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
| Duration | 2.30s |
| Avg Latency | 0.1054s |
| P50 Latency | 0.0573s |
| P99 Latency | 1.8291s |
| Throughput | 130.67 acked msg/s |
| Result | PASS |

### 150 客户端并发

命令：

```bash
python tests/stress_test.py --clients 150 --concurrency 75 --messages 3 --timeout 15
```

结果：

| 指标 | 数值 |
|------|------|
| Total Clients | 150 |
| Connected | 150 |
| Registered | 150 |
| Login Success | 150 |
| Messages Sent | 450 |
| Messages ACKed | 450 |
| Messages Received | 450 |
| Total Errors | 0 |
| Duration | 2.85s |
| Avg Latency | 0.1760s |
| P50 Latency | 0.1430s |
| P99 Latency | 1.5088s |
| Throughput | 158.06 acked msg/s |
| Result | PASS |

### 100 客户端高消息量

命令：

```bash
python tests/stress_test.py --clients 100 --concurrency 50 --messages 10 --timeout 15
```

结果：

| 指标 | 数值 |
|------|------|
| Total Clients | 100 |
| Connected | 100 |
| Registered | 100 |
| Login Success | 100 |
| Messages Sent | 1000 |
| Messages ACKed | 1000 |
| Messages Received | 1000 |
| Total Errors | 0 |
| Duration | 3.22s |
| Avg Latency | 0.1083s |
| P50 Latency | 0.0753s |
| P99 Latency | 1.4989s |
| Throughput | 310.33 acked msg/s |
| Result | PASS |

### 200 客户端连接上限测试

`server/config.py` 中 `max_connections` 配置为 200。本次测试达到 200 客户端。

命令：

```bash
python tests/stress_test.py --clients 200 --concurrency 100 --messages 3 --timeout 20
```

结果：

| 指标 | 数值 |
|------|------|
| Total Clients | 200 |
| Connected | 200 |
| Registered | 200 |
| Login Success | 200 |
| Messages Sent | 600 |
| Messages ACKed | 600 |
| Messages Received | 600 |
| Total Errors | 0 |
| Duration | 3.27s |
| Avg Latency | 0.2248s |
| P50 Latency | 0.1803s |
| P99 Latency | 1.9931s |
| Throughput | 183.33 acked msg/s |
| Result | PASS |

## AI 测试结论

### Mock 单元测试

`tests/test_ai_service.py` 已通过 mock HTTP 行为验证：

- `BIGMODEL_API_KEY` 优先使用 BigModel 默认地址和模型。
- 仅 `DASHSCOPE_API_KEY` 时使用 DashScope OpenAI-compatible 默认地址和 `qwen-turbo`。
- `AI_API_BASE` / `AI_MODEL` 可覆盖默认值。
- 无 key、401、超时、网络错误、解析失败均有友好兜底。

### DeepSeek 真实 smoke test

本次真实 smoke test 使用 DeepSeek OpenAI-compatible 接口。测试进程临时设置：

```text
AI_API_BASE=https://api.deepseek.com
AI_MODEL=deepseek-chat
BIGMODEL_API_KEY=<由 DEEPSEEK_API_KEY 临时映射，不写入文件>
```

执行路径：

1. 连接测试服务端。
2. 注册并登录测试用户。
3. 创建群组。
4. 发送 `AI_QUERY`：`请用一句中文解释 TCP 三次握手。`
5. 等待服务端返回 `AI_RESP`。

结果：

```text
AI reply length: 60
AI reply preview: TCP三次握手是建立连接时，客户端和服务器通过三次消息交换（SYN、SYN-ACK、ACK）确认双方收发能力正常的过程。
```

结论：真实外部 AI 调用成功，服务端能收到 AI 回复并通过协议返回客户端。现场演示仍依赖网络、API Key 有效性和账户额度，演示前应重新做一次 smoke test。

### AI 功能演示证据说明

GUI 演示中，任意群成员在群聊输入：

```text
@AI 请用一句话解释 TCP 三次握手。
```

预期结果：

- 服务端接收 `AI_QUERY` 并调用 `AIService.query_with_context()`。
- AI 回复以绿色 AI 气泡显示在当前群聊。
- 同一群组内其他在线成员也能看到该 AI 回复。
- AI 回复不会同步到用户私聊或 AI assistant 侧栏的未读提示中。

若现场网络或 API Key 不可用，服务端会返回“AI 服务未配置”或类似友好错误；该场景只说明外部 AI 环境不可用，不影响普通聊天、群聊、文件和内容审核功能。

## 内容审核测试结论

内容审核通过 `tests/test_content_moderator.py` 验证：

- 普通文本放行。
- 中风险词汇，如 `fuck`、`shit`、`色情`，可识别为不当言论。
- 高风险词汇，如 `attack`、`杀了你`，可识别为高风险并阻止。
- 英文大小写归一化，`Fuck`、`SB` 等变体可被识别。

### 内容审核功能演示证据说明

演示时可发送普通消息和敏感词消息对比：

| 输入类型 | 示例 | 预期结果 |
|----------|------|----------|
| 普通文本 | `今天网络实验正常进行。` | 消息正常发送和接收 |
| 中风险词 | 包含中风险词汇的普通聊天文本 | 服务端返回内容警告 |
| 高风险词 | 包含高风险攻击词汇的文本 | 服务端拦截，不写入正常聊天记录 |

内容审核在服务端执行，能够防止被拦截内容继续转发给其他客户端。

## 服务器断线与心跳结论

已通过集成测试验证客户端发送 `HEARTBEAT` 后服务端返回 `HEARTBEAT_ACK`。

客户端 `client/connection.py` 具备断线检测与指数退避重连逻辑。GUI 客户端在服务端断开后会把顶部连接状态从 Connected 切换为 Disconnected，左上角用户状态从绿点 `Online` 切换为红点 `Offline`，并阻止用户误以为离线消息已成功发出。服务端恢复后，客户端会自动重连、重新登录并刷新在线用户和群组状态。

CLI 中断线时会显示：

```text
Disconnected. Reconnecting...
Reconnected.
```

## P2P 说明

P2P 打洞代码保留为实验性扩展，但没有在复杂 NAT、防火墙或校园网隔离环境下验证。最终演示建议主打服务端中继文件传输，P2P 作为扩展说明，不作为必成演示路径。

## 改进建议

1. **修复默认运行数据库启动问题**

   当前 `server/data/chat.db` 为 0 字节，并且工作区 D 盘路径下 SQLite 建表会触发 `disk I/O error`。建议演示前处理：

   - 关闭所有服务端进程。
   - 备份或删除 `server/data/chat.db`、`server/data/chat.db-journal`。
   - 在普通终端重新运行 `python -m server.main` 验证默认路径能否启动。
   - 如果 D 盘目录仍报 SQLite I/O 错误，可临时把项目复制到本机系统盘，或修改 `ServerConfig.db_path` 指向可正常写 SQLite 的目录。

2. **扩展弱网重连测试覆盖**

   GUI 客户端已支持断线提示、自动重连、自动重新登录、在线用户刷新和群组状态恢复。后续可以继续补充更复杂的弱网测试，例如多次连续断线、服务端长时间不可用后恢复、断线期间多客户端同时发送失败等场景。

3. **扩展压力测试维度**

   当前 `tests/stress_test.py` 主要验证自发自收私聊 ACK。建议增加：

   - 多用户交叉私聊。
   - 大群广播压力。
   - 大文件/多文件并发传输压力。
   - 离线消息堆积后登录拉取压力。

4. **文件传输断点续传能力需要更明确**

   服务端已支持分块、重复 chunk 幂等和按 offset 下载，但客户端常规发送流程是一次性顺序发送文件。若要严格满足“断点续传”，建议在客户端加入失败后查询进度、从缺失 chunk 继续发送/下载的交互或自动恢复逻辑。

5. **演示前重新做 AI smoke test**

   AI 真实接口依赖网络、API Key 和额度。演示前建议先用一个短问题验证 `@AI`，并准备“无 Key 时显示友好错误”的备用展示路径。

## 总体结论

在隔离可写数据库路径下，项目核心功能通过端到端测试：注册、登录、私聊、群聊、在线用户、撤回、历史和心跳均通过。压力测试达到作业要求的 50 客户端并发和 100 用户同时在线，并进一步通过 150 客户端、100 客户端 1000 条消息、200 客户端连接上限测试。真实 DeepSeek AI smoke test 成功返回中文回答。当前主要风险集中在本机工作区 SQLite I/O 环境，以及文件传输“断点续传”语义还不够完整；演示时建议使用可正常写入 SQLite 的目录并优先展示服务端中继文件传输。
