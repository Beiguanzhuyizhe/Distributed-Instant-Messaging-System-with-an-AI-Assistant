服务端：server
asyncio TCP 服务器、用户注册/登录、在线状态、私聊/群聊路由、群组管理、心跳检测、SQLite 存储、历史消息、消息撤回、服务端中继文件传输、内容审核、AI 调用、P2P 辅助打洞。

客户端：client
CLI 和 tkinter GUI 两种客户端，支持登录注册、私聊、群聊、在线用户、历史记录、文件发送、AI 提问、本地 JSON 消息存储。

协议文档：docs/protocol.md
定义了 12 字节 TCP 二进制头部 + JSON payload，覆盖登录、注册、私聊、群聊、心跳、文件、撤回、AI、历史、P2P 等消息类型。

设计/测试文档：docs/design.md、docs/test_report.md、docs/user_manual.md

测试：tests
有协议单测、数据库测试、集成测试脚本、压力测试脚本。文档里写过 52 个单测通过、100 并发压测通过，但我本机没装 pytest，所以没法复现 pytest 结果。我跑了 python -m compileall server client tests，语法编译是通过的。