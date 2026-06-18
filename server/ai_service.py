"""
@AI 智能回复模块
调用 BigModel / DashScope OpenAI-compatible API。
API Key 由 ServerConfig 从 BIGMODEL_API_KEY 或 DASHSCOPE_API_KEY 读取。
"""

import json
import asyncio
import logging
from typing import Optional

import aiohttp
import aiohttp.client_exceptions

from server.config import ServerConfig

logger = logging.getLogger(__name__)


class AIService:
    """
    AI 智能回复服务
    通过 aiohttp 异步调用大模型 API，不阻塞事件循环
    默认优先使用 BigModel；仅配置 DashScope Key 时使用 DashScope 兼容接口。
    """

    def __init__(self, config: Optional[ServerConfig] = None):
        self.config = config or ServerConfig()
        self._api_key = self.config.ai_api_key or ""
        self._api_base = getattr(self.config, "ai_api_base",
                                 "https://open.bigmodel.cn/api/paas/v4")
        self._model = self.config.ai_model or "glm-4-flash-250414"
        self._timeout = 15  # 秒

        # 系统提示词：定义 AI 身份
        self._system_prompt = {
            "role": "system",
            "content": (
                "你是一个友好、乐于助人的聊天助手。"
                "你在一个即时通讯群组中回答问题。"
                "请用中文回答，保持回答简洁明了（一般不超过200字）。"
                "如果不知道答案，请如实告知。"
            )
        }

        if not self._api_key:
            logger.warning("BIGMODEL_API_KEY/DASHSCOPE_API_KEY 未设置，AI 服务将不可用")

    @property
    def available(self) -> bool:
        """AI 服务是否可用（API Key 已配置）"""
        return bool(self._api_key)

    async def query(self, prompt: str, history: Optional[list] = None) -> str:
        """
        调用 AI API 获取智能回复

        Args:
            prompt: 用户提问内容
            history: 可选的历史消息列表，格式 [{"role": "user"|"assistant", "content": str}, ...]

        Returns:
            回复文本，失败时返回错误提示
        """
        if not self._api_key:
            return "AI 服务未配置（请设置 BIGMODEL_API_KEY 或 DASHSCOPE_API_KEY）"

        if not prompt or not prompt.strip():
            return "请输入有效的问题"

        messages = [self._system_prompt]

        # 添加历史消息（最多 10 条）
        if history:
            for msg in history[-10:]:
                if isinstance(msg, dict) and "role" in msg and "content" in msg:
                    messages.append(msg)

        # 添加当前问题
        messages.append({"role": "user", "content": prompt.strip()})

        # 兼容 OpenAI 格式的请求体
        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": 512,
            "temperature": 0.7,
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        url = f"{self._api_base.rstrip('/')}/chat/completions"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"AI API 返回错误 (HTTP {resp.status}): {error_text}")
                        return self._error_message(resp.status)

                    result = await resp.json()
                    return self._extract_reply(result)

        except asyncio.TimeoutError:
            logger.warning("AI API 请求超时")
            return "AI 服务暂时不可用（请求超时）"
        except aiohttp.client_exceptions.ClientError as e:
            logger.error(f"AI API 请求失败: {e}")
            return "AI 服务暂时不可用（网络错误）"
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(f"AI API 返回解析失败: {e}")
            return "AI 服务暂时不可用（响应解析失败）"

    def _extract_reply(self, result: dict) -> str:
        """从 API 响应中提取回复文本"""
        # OpenAI 兼容格式
        choices = result.get("choices", [])
        if not choices:
            return "AI 服务暂时不可用（返回为空）"

        message = choices[0].get("message", {})
        content = message.get("content", "")
        if not content:
            content = choices[0].get("text", "")

        return content.strip() or "AI 服务暂时不可用（返回为空）"

    def _error_message(self, status_code: int) -> str:
        """根据 HTTP 状态码返回用户友好的错误信息"""
        if status_code == 401:
            return "AI 服务认证失败（API Key 无效）"
        elif status_code == 429:
            return "AI 服务请求过于频繁，请稍后再试"
        elif status_code >= 500:
            return "AI 服务暂时不可用（服务端错误）"
        else:
            return f"AI 服务暂时不可用（错误码: {status_code})"

    async def query_with_context(
        self,
        prompt: str,
        username: str = "",
        history: Optional[list] = None,
    ) -> str:
        """
        带上下文的 AI 查询

        Args:
            prompt: 用户问题
            username: 提问者用户名（用于个性化）
            history: 最近聊天历史
        """
        effective_history = list(history or [])
        if username:
            effective_history.append({
                "role": "system",
                "content": (
                    f"当前提问者用户名是 {username}。这条信息仅用于理解上下文。"
                    "你必须始终以 AI Assistant 身份回答，不要自称为该用户，"
                    "不要扮演该用户，也不要在回复开头添加“用户名：”这类前缀。"
                ),
            })

        return await self.query(prompt, effective_history)


# 全局单例
_service: Optional[AIService] = None


def get_ai_service(config: Optional[ServerConfig] = None) -> AIService:
    global _service
    if _service is None:
        _service = AIService(config)
    return _service
