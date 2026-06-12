# 即时聊天系统 - 用户手册

## 一、系统概述

分布式即时聊天系统，支持文字聊天、群组聊天、AI 智能回复、服务端中继文件传输等功能。

### 技术栈
- **语言**: Python 3.10+
- **网络**: TCP（文字/控制消息、服务端中继文件传输）+ UDP（实验性 P2P）
- **数据库**: SQLite（服务端持久化）/ JSON（客户端本地存储）
- **AI**: BigModel 优先，兼容 DashScope OpenAI-compatible 接口
- **界面**: CLI (rich) + GUI (tkinter) 双模

---

## 二、环境要求

- Python 3.10 或更高版本
- 操作系统：Windows / Linux / macOS

### 依赖安装

```bash
python -m pip install -r requirements.txt
```

---

## 三、快速启动

### 3.1 启动服务器

```bash
cd 项目根目录
python -m server.main
```

服务器默认在 `0.0.0.0:8888` 监听。可通过修改 `server/config.py` 更改端口。
如果使用 Windows 启动脚本 `先点我启动服务器-restart.bat`，脚本会直接读取当前环境变量中的 `BIGMODEL_API_KEY` 或 `DASHSCOPE_API_KEY`，不会在脚本内保存 API Key。

成功启动日志：
```
2026-04-30 [INFO] server.heartbeat: Heartbeat monitor started (interval=30s, timeout=90s)
2026-04-30 [INFO] server.tcp_server: ChatServer listening on 0.0.0.0:8888
```

### 3.2 启动客户端（CLI 模式）

```bash
python -m client.main --cli
```

### 3.3 启动客户端（GUI 模式）

```bash
python -m client.main --gui
```

---

## 四、CLI 客户端使用指南

### 4.1 注册 / 登录

启动客户端后，首先进入登录界面：

```
╔══════════════════════════════════════╗
║          即时聊天系统 v1.0            ║
╠══════════════════════════════════════╣
║  1. 登录                             ║
║  2. 注册                             ║
║  3. 退出                             ║
╚══════════════════════════════════════╝
```

选择 **2** 注册新账号（用户名需唯一），注册成功后选择 **1** 登录。

### 4.2 主界面

登录后进入聊天主界面，分为三个区域：
- **顶部**: 系统状态栏（显示用户名、连接状态）
- **中部**: 消息列表（实时显示聊天消息）
- **底部**: 命令输入框

### 4.3 可用命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/msg <用户> <内容>` | 发送私聊消息 | `/msg alice 你好!` |
| `/create <群名>` | 创建群组 | `/create 技术交流群` |
| `/join <群ID>` | 加入群组 | `/join 1` |
| `/leave <群ID>` | 退出群组 | `/leave 1` |
| `/group <群ID> <内容>` | 发送群聊消息 | `/group 1 大家好!` |
| `@AI <问题>` | 向 AI 提问（需在群聊中） | `@AI 什么是 TCP 协议?` |
| `/users` | 查看在线用户 | `/users` |
| `/sendfile <用户> <文件路径>` | 发送文件 | `/sendfile bob ./doc.pdf` |
| `/recall <消息ID>` | 撤回消息（2分钟内，使用服务端确认的 UUID） | `/recall 6f1b1e8d-9c84-4e6f-9d8c-123456789abc` |
| `/history <用户或群ID>` | 查看历史消息；私聊可输入在线用户名，群聊输入群 ID | `/history alice` |
| `/quit` | 退出程序 | `/quit` |

### 4.4 消息撤回

发送消息后，客户端会在服务端确认时显示一行消息 ID：
```
Message confirmed: 6f1b1e8d-9c84-4e6f-9d8c-123456789abc
```

2 分钟内可使用该 ID 撤回：
```
/recall 6f1b1e8d-9c84-4e6f-9d8c-123456789abc
```
系统显示：`[系统] Alice 撤回了一条消息`

撤回成功后，客户端本地历史会同步把该消息标记为“已撤回”。如果超过 2 分钟或尝试撤回他人消息，服务端会返回失败提示。

### 4.5 历史记录

CLI 支持从服务器拉取私聊或群聊历史：

```bash
/history alice
/history 1
```

当当前会话是私聊时，`/history alice` 会把在线用户名解析为用户 ID 后向服务端查询；当当前会话是群聊时，`/history 1` 查询 1 号群的历史。历史消息会逐条显示，并附带可用于撤回的服务端 `msg_id`。

### 4.6 文件传输

默认演示和日常使用建议选择服务端中继模式：文件经过服务器转发，适合所有网络环境。
```
/sendfile bob ./document.pdf
```

项目中保留了 P2P UDP 打洞实验代码，可在同机或局域网环境尝试。P2P 成功率受 NAT、防火墙和校园网隔离影响，现场演示不建议把它作为必成路径。

### 4.7 @AI 智能回复

在群聊中使用 `@AI` 触发 AI 回复：
```
@AI 请用简单的话解释 TCP 三次握手
```

系统会显示 AI 的回复消息。

---

## 五、GUI 客户端使用指南

### 5.1 登录窗口

启动 GUI 后，首先显示登录窗口：
- 输入用户名和密码
- 点击"登录"或"注册"按钮

### 5.2 主窗口

登录成功后进入主界面：
- **左侧面板**: 联系人列表 + 群组列表（在线用户有绿色标识）
- **右侧面板**: 聊天区域
  - 消息以气泡形式显示
  - 底部输入框输入消息
  - 点击"发送"按钮或按 Enter 发送

### 5.3 功能操作

- **私聊**: 双击左侧联系人打开私聊窗口
- **群聊**: 在左侧选择群组，右侧显示群聊
- **文件传输**: 点击工具栏文件图标选择文件
- **@AI**: 在输入框中以 `@AI` 开头输入问题
- **历史记录**: 菜单栏选择 `Chat -> Load History`
- **消息撤回**: 菜单栏选择 `Chat -> Recall Last Sent`，撤回最近一条已由服务端确认的本人消息

---

## 六、AI 功能配置

### 设置 API Key

AI 功能优先使用 BigModel；如果只设置了 `DASHSCOPE_API_KEY`，服务端会默认切换到 DashScope OpenAI-compatible 接口。

```bash
# Windows (CMD)
set BIGMODEL_API_KEY=your_api_key_here

# Windows (PowerShell)
$env:BIGMODEL_API_KEY="your_api_key_here"

# Linux / macOS
export BIGMODEL_API_KEY=your_api_key_here
```

也可以使用 DashScope：

```bash
# Windows (PowerShell)
$env:DASHSCOPE_API_KEY="your_api_key_here"
```

高级配置可用 `AI_API_BASE` / `AI_MODEL` 覆盖默认接口地址和模型名。详见 `docs/ai_setup.md`。

---

## 七、故障排除

| 问题 | 可能原因 | 解决方法 |
|------|---------|---------|
| 连接失败 | 服务器未启动 | 确认已运行 `python -m server.main` |
| 连接被拒绝 | 端口错误 | 检查服务器端口（默认 8888） |
| 注册失败（用户名已存在） | 用户名被占用 | 更换用户名 |
| AI 无回复 | API Key 未设置 | 设置 `BIGMODEL_API_KEY` 或 `DASHSCOPE_API_KEY` 环境变量 |
| AI 回复超时 | 网络问题 | 检查网络连接，稍后重试 |
| 文件传输失败 | 文件路径错误 | 确认文件路径正确 |
| P2P 传输失败 | NAT/防火墙/局域网隔离 | 使用默认服务端中继文件传输 |
| GUI 界面无法启动 | tkinter 未安装 | `pip install tk`（Linux 需额外安装） |

---

## 八、卸载

直接删除项目目录即可。用户数据存储在：
- 服务端：`server/data/chat.db`（SQLite 数据库）
- 客户端：`client/data/`（JSON 消息历史文件）
