# 手动测试与录屏指南

本文档用于课程大作业现场手动测试和录制演示视频。演示重点对应 `作业要求.pdf`：3 个客户端同时通信、文字聊天、文件传输、AI 或内容审核、服务器断线重连、并发压力测试结果。

## 录制前准备

### 1. 打开终端并进入项目目录

```powershell
cd "D:\Courses Learning\Computer Network\final_work"
```

### 2. 安装依赖

```powershell
python -m pip install -r requirements.txt
```

如需完整运行测试，建议使用本次验证过的 pytest 版本：

```powershell
python -m pip install pytest==8.3.4 pytest-asyncio==0.23.8
```

### 3. 准备 AI 环境变量

如果使用 DeepSeek 做真实 AI 演示，不要把 API Key 写入任何项目文件。只在当前 PowerShell 会话中临时设置：

```powershell
$env:BIGMODEL_API_KEY = [Environment]::GetEnvironmentVariable("DEEPSEEK_API_KEY", "User")
$env:AI_API_BASE = "https://api.deepseek.com"
$env:AI_MODEL = "deepseek-chat"
```

检查是否读到 Key 时只检查布尔值，不打印 Key：

```powershell
python -c "import os; print(bool(os.environ.get('BIGMODEL_API_KEY')))"
```

输出 `True` 表示当前终端能使用 AI；输出 `False` 时，本次演示可以改为展示“AI 服务未配置”的友好错误。

### 4. 处理默认数据库启动风险

本次自动化测试发现当前工作区内 SQLite 文件可能出现 `disk I/O error`。正式录屏前先测试默认启动：

```powershell
python -m server.main
```

如果正常看到类似输出：

```text
ChatServer started on 0.0.0.0:8888
```

说明可以直接继续录制。

如果出现：

```text
sqlite3.OperationalError: disk I/O error
```

执行以下处理后重试：

1. 关闭所有正在运行的服务端窗口。
2. 备份或删除运行期数据库文件：

   ```powershell
   Rename-Item "server\data\chat.db" "chat.db.bak" -ErrorAction SilentlyContinue
   Rename-Item "server\data\chat.db-journal" "chat.db-journal.bak" -ErrorAction SilentlyContinue
   ```

3. 重新执行：

   ```powershell
   python -m server.main
   ```

如果仍失败，可把项目复制到系统盘的普通英文路径再录制，例如：

```text
C:\chat_final_work
```

然后在新路径中重新运行服务端。这个处理只影响运行期数据，不影响源码和文档。

## 推荐录屏窗口布局

建议同时打开 4 个终端窗口：

| 窗口 | 用途 |
|------|------|
| 窗口 1 | 服务端 |
| 窗口 2 | 客户端 Alice |
| 窗口 3 | 客户端 Bob |
| 窗口 4 | 客户端 Carol 或压测命令 |

窗口标题可手动改成：

- `Server`
- `Alice`
- `Bob`
- `Carol`

这样视频里更容易看清每个角色。

## 启动服务端

在窗口 1 执行：

```powershell
cd "D:\Courses Learning\Computer Network\final_work"
python -m server.main
```

确认看到：

```text
ChatServer started on 0.0.0.0:8888
```

此时不要关闭窗口 1。

## 启动 3 个 CLI 客户端

分别在窗口 2、窗口 3、窗口 4 执行：

```powershell
cd "D:\Courses Learning\Computer Network\final_work"
python -m client.main --cli
```

如果 GUI 更适合展示，也可以把其中一个窗口换成：

```powershell
python -m client.main --gui
```

CLI 更适合稳定录制命令和输出。

## 注册与登录

为了避免用户名与旧数据库冲突，建议使用带日期或随机后缀的账号。例如：

| 角色 | 用户名 | 密码 |
|------|--------|------|
| Alice | `alice_demo_0614` | `123456` |
| Bob | `bob_demo_0614` | `123456` |
| Carol | `carol_demo_0614` | `123456` |

每个客户端进入菜单后：

1. 选择注册。
2. 输入用户名。
3. 输入密码。
4. 注册成功后选择登录。
5. 使用同一用户名和密码登录。

登录成功后，窗口中会进入聊天主界面。

## 在线用户测试

在 Alice 客户端输入：

```text
/users
```

预期现象：

- 能看到 Alice、Bob、Carol 至少 3 个在线用户。
- 这个步骤对应作业要求中的“用户在线状态显示”和服务端“在线状态同步”。

## 一对一私聊测试

在 Alice 客户端输入：

```text
/msg bob_demo_0614 你好 Bob，这是 Alice 发来的私聊消息。
```

预期现象：

- Alice 侧显示消息发送和服务端确认。
- Bob 侧收到 Alice 的私聊消息。
- Alice 侧通常会显示服务端确认的消息 ID，后续撤回会用到。

在 Bob 客户端回复：

```text
/msg alice_demo_0614 收到，这是 Bob 的回复。
```

预期现象：

- Alice 收到 Bob 的回复。
- 这个步骤对应“一对一文字聊天”和“消息路由”。

## 群聊测试

### 1. Alice 创建群组

在 Alice 客户端输入：

```text
/create demo_group
```

预期现象：

- Alice 侧显示创建成功。
- 记录输出中的 `group_id`，下面用 `<群ID>` 表示。

### 2. Bob 和 Carol 加入群组

在 Bob 客户端输入：

```text
/join <群ID>
```

在 Carol 客户端输入：

```text
/join <群ID>
```

预期现象：

- Bob 和 Carol 都显示加入成功。

### 3. 三人群聊

在 Alice 客户端输入：

```text
/group <群ID> 大家好，这是 Alice 在群里的消息。
```

在 Bob 客户端输入：

```text
/group <群ID> Bob 已加入群聊。
```

在 Carol 客户端输入：

```text
/group <群ID> Carol 也收到群消息。
```

预期现象：

- 其他群成员能收到群消息。
- 这个步骤对应“群组聊天、创建/加入群组、消息路由”。

## 消息历史测试

在 Alice 客户端查询与 Bob 的私聊历史：

```text
/history bob_demo_0614
```

预期现象：

- 能看到刚才 Alice 和 Bob 的私聊消息。

在 Alice 客户端查询群聊历史：

```text
/history <群ID>
```

如果当前会话不是群聊，先发一条群消息让当前会话切到群聊：

```text
/group <群ID> 准备查询群聊历史。
/history <群ID>
```

预期现象：

- 能看到群聊消息记录。
- 这个步骤对应“消息历史记录，本地或服务器存储”。

## 消息撤回测试

在 Alice 客户端发送一条私聊：

```text
/msg bob_demo_0614 这条消息马上撤回。
```

观察 Alice 侧输出，找到服务端确认的 `msg_id`，通常是 UUID 格式，例如：

```text
6f1b1e8d-9c84-4e6f-9d8c-123456789abc
```

在 2 分钟内输入：

```text
/recall <msg_id>
```

预期现象：

- Alice 侧显示撤回成功。
- Bob 侧收到撤回通知。
- 这个步骤对应“消息撤回功能，2 分钟内可撤回”。

## 文件传输测试

### 1. 准备测试文件

在项目根目录新建一个小文件：

```powershell
Set-Content -Path ".\demo_send.txt" -Value "这是文件传输演示内容。" -Encoding UTF8
```

### 2. Alice 发送文件给 Bob

在 Alice 客户端输入：

```text
/sendfile bob_demo_0614 .\demo_send.txt
```

预期现象：

- Alice 侧显示正在发送和发送完成。
- Bob 侧显示收到文件通知并自动下载。
- 下载文件默认保存在：

```text
client\downloads\
```

### 3. 检查下载文件

在一个普通 PowerShell 窗口执行：

```powershell
Get-ChildItem ".\client\downloads"
Get-Content ".\client\downloads\demo_send.txt" -Encoding UTF8
```

预期现象：

- 能看到 `demo_send.txt`。
- 文件内容与发送端一致。
- 这个步骤对应“文件发送与接收功能”。

说明：当前系统已经支持分块传输、重复 chunk 幂等、按 offset 下载和权限校验；常规客户端流程是一次性顺序发送。严格意义上的“断点续传自动恢复”还可以作为后续改进点说明。

## 内容审核测试

内容审核在服务端启用。建议演示两个样例：

### 1. 中风险词汇

在 Alice 客户端输入：

```text
/msg bob_demo_0614 this is fuck test
```

预期现象：

- 服务端识别不当言论。
- 消息可能被替换、警告或拦截，具体显示取决于当前消息路由处理。

### 2. 高风险词汇

在 Alice 客户端输入：

```text
/msg bob_demo_0614 attack test
```

预期现象：

- 服务端识别高风险内容。
- 消息应被拦截或返回错误提示。

这个步骤对应扩展功能“智能内容审核”。

## AI 智能助手测试

### 1. 确认 AI 环境变量

在服务端启动前的 PowerShell 中执行：

```powershell
$env:BIGMODEL_API_KEY = [Environment]::GetEnvironmentVariable("DEEPSEEK_API_KEY", "User")
$env:AI_API_BASE = "https://api.deepseek.com"
$env:AI_MODEL = "deepseek-chat"
python -c "import os; print(bool(os.environ.get('BIGMODEL_API_KEY')))"
```

输出 `True` 后，在同一个窗口启动服务端：

```powershell
python -m server.main
```

### 2. 在群聊中触发 AI

先确保 Alice 当前在群聊会话中。可以先发：

```text
/group <群ID> 准备测试 AI。
```

然后输入：

```text
@AI 请用一句话解释 TCP 三次握手。
```

预期现象：

- Alice 收到 AI 回复。
- 群内其他成员也能看到 AI 回复或相关广播。
- 这个步骤对应“服务端集成第三方 AI 服务，当用户在群聊发送特定指令时调用模型接口并转发回群组”。

### 3. 无 Key 备用展示

如果现场网络或 API Key 不可用，可以不设置 AI 环境变量，直接发送：

```text
@AI 请解释 TCP。
```

预期现象：

- 服务端返回“AI 服务未配置”或类似友好错误。
- 这可作为异常处理展示，但不如真实回复完整。

## 服务器断线重连测试

当前客户端底层支持 TCP 自动重连，但重连后不会自动重新登录。推荐按下面方式录制，既展示断线检测，也避免把未实现的自动恢复说成已实现。

### 1. 正常通信

Alice 给 Bob 发一条消息：

```text
/msg bob_demo_0614 断线测试前的消息。
```

确认 Bob 收到。

### 2. 停止服务端

在服务端窗口按：

```text
Ctrl+C
```

预期现象：

- 客户端显示断线或正在重连，例如 `Disconnected. Reconnecting...`。

### 3. 重启服务端

在服务端窗口重新执行：

```powershell
python -m server.main
```

预期现象：

- 客户端可能显示 `Reconnected.`。
- 如果消息发送提示未登录或无响应，重启客户端并重新登录。

### 4. 恢复通信

重新登录 Alice 和 Bob 后，再发送：

```text
/msg bob_demo_0614 服务端重启后恢复通信。
```

预期现象：

- Bob 收到消息。

录制讲解建议：

```text
客户端能够检测 TCP 断线并尝试重连。当前版本重连后还需要重新登录恢复认证态，这是后续可以改进为自动重新登录的点。
```

## 压力测试展示

压力测试不需要录完整过程，可以录制运行命令和最终报告。

### 1. 启动服务端

在服务端窗口运行：

```powershell
python -m server.main
```

如果默认数据库路径仍受 SQLite I/O 问题影响，可展示 `docs/test_report.md` 中本次已完成的隔离数据库压力测试结果。

### 2. 50 客户端并发

在另一个窗口运行：

```powershell
python tests/stress_test.py --clients 50 --concurrency 20 --messages 3 --timeout 10
```

预期结果：

```text
Result: PASS
Connected: 50
Login Success: 50
Total Errors: 0
```

### 3. 100 用户同时在线

运行：

```powershell
python tests/stress_test.py --clients 100 --concurrency 50 --messages 3 --timeout 10
```

预期结果：

```text
Result: PASS
Connected: 100
Login Success: 100
Total Errors: 0
```

### 4. 更高压力展示

本次报告中已验证：

```powershell
python tests/stress_test.py --clients 100 --concurrency 50 --messages 10 --timeout 15
python tests/stress_test.py --clients 200 --concurrency 100 --messages 3 --timeout 20
```

对应结果：

- 100 客户端、1000 条消息：PASS。
- 200 客户端、600 条消息：PASS。

## 推荐视频顺序

1. 展示项目目录和文档：`README.md`、`docs/design.md`、`docs/protocol.md`、`docs/test_report.md`。
2. 启动服务端。
3. 启动 Alice、Bob、Carol 三个客户端。
4. 三个账号注册/登录。
5. `/users` 展示在线用户。
6. Alice 和 Bob 私聊。
7. Alice 创建群，Bob 和 Carol 加入群。
8. 三人在群里发消息。
9. 查询历史记录。
10. 撤回一条消息。
11. 发送并接收文件。
12. 演示内容审核。
13. 演示 `@AI`。
14. 停止并重启服务端，展示断线和恢复通信。
15. 展示压力测试命令和 `docs/test_report.md` 中的 100/200 客户端结果。

## 讲解要点

录屏讲解时可以围绕以下点说明：

- 系统采用客户端-服务器架构，TCP 负责文字、控制消息和服务端中继文件传输。
- 协议是 12 字节二进制头部加 JSON payload，能处理粘包和半包。
- 服务端使用 asyncio 处理并发连接，SQLite 存储用户、消息、群组和文件传输记录。
- 客户端有 CLI 和 GUI 两种模式，CLI 适合稳定演示。
- 内容审核使用 Aho-Corasick 多模式匹配，适合快速匹配多关键词。
- AI 接口使用 OpenAI-compatible 格式，能通过 `AI_API_BASE` 和 `AI_MODEL` 适配不同供应商。
- 压力测试已达到作业要求的 50 客户端并发和 100 用户同时在线，并额外通过 200 客户端测试。

## 演示前检查清单

| 检查项 | 通过标准 |
|--------|----------|
| 依赖安装 | `python -m pip install -r requirements.txt` 成功 |
| 服务端启动 | 出现 `ChatServer started on 0.0.0.0:8888` |
| 3 个客户端登录 | Alice、Bob、Carol 均能登录 |
| 在线用户 | `/users` 能看到 3 个用户 |
| 私聊 | Bob 能收到 Alice 私聊 |
| 群聊 | 群成员能收到群消息 |
| 文件传输 | Bob 的 `client\downloads` 中出现文件 |
| 历史记录 | `/history` 能看到历史消息 |
| 撤回 | 2 分钟内使用 `msg_id` 撤回成功 |
| 内容审核 | 敏感词触发警告或拦截 |
| AI | `@AI` 返回真实回复或友好错误 |
| 断线重连 | 客户端显示断线/重连，重新登录后恢复通信 |
| 压测结果 | 50/100 客户端压测 `Result: PASS` |
