# AI 服务配置说明

## 目标

`@AI` 功能通过服务端异步调用 OpenAI-compatible Chat Completions 接口实现。服务端不会在代码或 bat 脚本中保存 API Key，只从环境变量读取。

## 环境变量优先级

1. 如果设置了 `BIGMODEL_API_KEY`，优先使用 BigModel。
2. 如果没有 `BIGMODEL_API_KEY`，但设置了 `DASHSCOPE_API_KEY`，使用 DashScope OpenAI-compatible 接口。
3. `AI_API_BASE` 和 `AI_MODEL` 可以手动覆盖默认接口地址和模型名。

默认值：

| 场景 | 默认 API Base | 默认模型 |
|------|---------------|----------|
| `BIGMODEL_API_KEY` | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash-250414` |
| 仅 `DASHSCOPE_API_KEY` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-turbo` |

## Windows PowerShell 示例

```powershell
$env:BIGMODEL_API_KEY="your_bigmodel_key"
python -m server.main
```

或：

```powershell
$env:DASHSCOPE_API_KEY="your_dashscope_key"
python -m server.main
```

手动指定兼容接口：

```powershell
$env:AI_API_BASE="https://example.com/v1"
$env:AI_MODEL="custom-model"
python -m server.main
```

## 失败兜底

`server/ai_service.py` 已覆盖以下情况：

- 未配置 API Key：返回“AI 服务未配置”提示
- HTTP 401：返回认证失败提示
- HTTP 429：返回请求过于频繁提示
- HTTP 5xx：返回服务端暂不可用提示
- 请求超时：返回超时提示
- 网络错误：返回网络错误提示
- 响应解析失败：返回解析失败提示

相关单元测试：`tests/test_ai_service.py`。测试通过 mock HTTP 行为，不依赖真实 API Key。

## 安全要求

- 不要把 API Key 写入 `.bat`、`.py`、`.md` 或截图。
- `.claude/`、`.env`、`*.env` 已在 `.gitignore` 中忽略。
- 交付压缩包前仍应人工确认 `.claude/`、`agent-workspace/` 不被打包。
