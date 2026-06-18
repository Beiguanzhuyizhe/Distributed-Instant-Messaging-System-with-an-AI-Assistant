# 课程大作业提交文档清单

本文档用于对应课程大作业要求中的文档交付项，便于老师或助教快速定位设计、测试和使用说明。

## 1. 设计文档

| 要求 | 对应文件 | 说明 |
|------|----------|------|
| 系统架构图 | `docs/design.md` 第 1 节 | 展示客户端、服务端、数据库、AI、内容审核、文件传输、P2P 扩展之间的关系。 |
| 协议定义 | `docs/protocol.md` 第 1、2、3 节 | 定义 12 字节二进制头部、消息类型、错误码、交互流程和 JSON payload。 |
| 消息格式示例 | `docs/protocol.md` 第 3 节和“典型消息示例” | 覆盖登录、私聊、群聊、文件传输、AI、内容审核和错误响应。 |
| AI 功能实现逻辑 | `docs/design.md` 第 5 节 | 说明 `@AI` 触发、服务端异步调用 OpenAI-compatible 接口、群聊广播和异常兜底。 |

## 2. 测试报告

| 要求 | 对应文件 | 说明 |
|------|----------|------|
| 并发压力测试结果 | `docs/test_report.md` “压力测试” | 包含 50、100、150、200 客户端，以及 100 客户端 1000 条消息测试结果。 |
| 100 用户同时在线 | `docs/test_report.md` “100 客户端并发” | 记录 `Connected=100`、`Login Success=100`、`Result=PASS`。 |
| 智能回复测试 | `docs/test_report.md` “AI 测试结论” | 包含 mock 测试和真实 DeepSeek smoke test 结论，不记录 API Key 原文。 |
| 内容审核测试 | `docs/test_report.md` “内容审核测试结论” | 覆盖普通文本、中风险词、高风险词和英文大小写归一化。 |

## 3. 用户手册

| 要求 | 对应文件 | 说明 |
|------|----------|------|
| 客户端安装 | `docs/user_manual.md` 第 2、3 节 | 说明 Python 版本、依赖安装、服务端和客户端启动方式。 |
| CLI 使用说明 | `docs/user_manual.md` 第 4 节 | 说明注册登录、私聊、群聊、文件、历史、撤回和 `@AI` 命令。 |
| GUI 使用说明 | `docs/user_manual.md` 第 5 节 | 说明登录、联系人、群组、文件、AI、历史和断线状态。 |
| AI 配置说明 | `docs/user_manual.md` 第 6 节、`docs/ai_setup.md` | 说明 API Key 只从环境变量读取，不写入代码或文档。 |

## 4. 其他辅助文档

| 文件 | 用途 |
|------|------|
| `docs/manual_test_and_video_guide.md` | 录制演示视频时的操作流程和验收点。 |
| `docs/security.md` | 协议安全边界、端到端加密和内容审核说明。 |
| `docs/p2p_notes.md` | P2P 打洞扩展说明和演示建议。 |
| `README.md` | 项目入口说明、快速启动和测试命令。 |

## 5. 提交安全说明

- API Key 不写入源码、文档、日志或测试报告；AI 功能只读取环境变量。
- 运行数据目录 `server/data/`、`server/file_storage/`、`client/data/`、`client/downloads/` 不作为提交内容。
- 测试缓存、Python 缓存和临时目录不作为提交内容。
