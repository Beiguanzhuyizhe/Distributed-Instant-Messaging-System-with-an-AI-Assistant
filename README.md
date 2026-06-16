# 融合 AI 智能助手的分布式即时聊天系统

这是计算机网络课程大作业工程，采用 Python 实现客户端-服务器架构即时聊天系统。

## 功能概览

- TCP 自定义二进制协议：12 字节头部 + JSON payload
- 用户注册/登录、在线状态同步、心跳检测
- 私聊、群聊、创建/加入/退出群
- SQLite 服务端消息存储、客户端 JSON 本地历史
- 消息撤回、历史记录查询
- 服务端中继文件传输
- `@AI` 智能助手：支持 BigModel / DashScope OpenAI-compatible 接口
- 关键词内容审核
- CLI 与 tkinter GUI 两种客户端
- 协议、数据库、内容审核、AI、文件传输、集成和压力测试脚本

## 环境准备

```bash
python -m pip install -r requirements.txt
```

## 启动

服务端：

```bash
python -m server.main
```

CLI 客户端：

```bash
python -m client.main --cli
```

GUI 客户端：

```bash
python -m client.main --gui
```

Windows 可直接运行 `先点我启动服务器-restart.bat` 启动服务端。该脚本只读取环境变量，不保存 API Key。

## AI 配置

BigModel 优先：

```powershell
$env:BIGMODEL_API_KEY="your_key"
```

只有 DashScope Key 时自动使用 DashScope 默认接口和 `qwen-turbo`：

```powershell
$env:DASHSCOPE_API_KEY="your_key"
```

可选覆盖：

```powershell
$env:AI_API_BASE="https://your-compatible-api/v1"
$env:AI_MODEL="your-model"
```

未配置 Key 时，聊天基础功能不受影响，AI 查询会返回友好错误提示。

## 测试

单元测试：

```bash
python -m pytest
```

集成测试需先启动服务端：

```bash
python tests/run_integration_tests.py
```

压力测试需先启动服务端：

```bash
python tests/stress_test.py --clients 50 --concurrency 20 --messages 3
python tests/stress_test.py --clients 100 --concurrency 50 --messages 3
```

最终本机复现结果见 `docs/test_report.md`。

## 文档入口

课程提交文档清单见 `docs/submission_checklist.md`，其中按作业要求对应到：

- `docs/design.md`：系统架构图、AI 功能实现逻辑。
- `docs/protocol.md`：协议定义和典型消息格式示例。
- `docs/test_report.md`：单元测试、集成测试、并发压力测试、AI 和内容审核测试结论。
- `docs/user_manual.md`：客户端安装与使用说明。
- `docs/manual_test_and_video_guide.md`：现场手动测试和录屏流程。

## 交付注意

- 不要提交或打包 `.claude/`、`agent-workspace/`、`__pycache__/`、`.pytest_cache/`。
- 不要提交或打包 `server/data/`、`server/file_storage/`、`client/data/`、`client/downloads/`、日志文件。
- P2P 代码作为实验性扩展说明，默认演示建议使用服务端中继文件传输。
