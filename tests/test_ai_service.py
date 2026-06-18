import asyncio
import json

import server.config as config_module
from server.config import (
    BIGMODEL_API_BASE,
    BIGMODEL_MODEL,
    DASHSCOPE_API_BASE,
    DASHSCOPE_MODEL,
    ServerConfig,
)
from server.ai_service import AIService


def _clear_ai_env(monkeypatch):
    for name in ("BIGMODEL_API_KEY", "DASHSCOPE_API_KEY", "AI_API_BASE", "AI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config_module, "_LOCAL_ENV_CACHE", {})


def test_bigmodel_key_takes_precedence(monkeypatch):
    _clear_ai_env(monkeypatch)
    monkeypatch.setenv("BIGMODEL_API_KEY", "bigmodel-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")

    config = ServerConfig()

    assert config.ai_api_key == "bigmodel-key"
    assert config.ai_api_base == BIGMODEL_API_BASE
    assert config.ai_model == BIGMODEL_MODEL


def test_dashscope_only_uses_dashscope_defaults(monkeypatch):
    _clear_ai_env(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")

    config = ServerConfig()

    assert config.ai_api_key == "dashscope-key"
    assert config.ai_api_base == DASHSCOPE_API_BASE
    assert config.ai_model == DASHSCOPE_MODEL


def test_ai_manual_overrides(monkeypatch):
    _clear_ai_env(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.setenv("AI_API_BASE", "https://example.test/v1")
    monkeypatch.setenv("AI_MODEL", "custom-model")

    config = ServerConfig()

    assert config.ai_api_base == "https://example.test/v1"
    assert config.ai_model == "custom-model"


def test_query_without_key_returns_friendly_message():
    service = AIService(ServerConfig(ai_api_key="", ai_api_base="http://unused", ai_model="unused"))

    result = asyncio.run(service.query("hello"))

    assert "AI 服务未配置" in result
    assert "BIGMODEL_API_KEY" in result
    assert "DASHSCOPE_API_KEY" in result


class _FakeResponse:
    def __init__(self, status=200, body=None, text=""):
        self.status = status
        self._body = body if body is not None else {}
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._text

    async def json(self):
        if isinstance(self._body, BaseException):
            raise self._body
        return self._body


class _TimeoutResponse:
    async def __aenter__(self):
        raise asyncio.TimeoutError()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, response, requests):
        self._response = response
        self._requests = requests

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, headers, json, timeout):
        self._requests.append(
            {"url": url, "headers": headers, "json": json, "timeout": timeout}
        )
        return self._response


def _patch_session(monkeypatch, response):
    requests = []

    def factory():
        return _FakeSession(response, requests)

    monkeypatch.setattr("server.ai_service.aiohttp.ClientSession", factory)
    return requests


def test_query_success_uses_openai_compatible_payload(monkeypatch):
    response = _FakeResponse(body={"choices": [{"message": {"content": "pong"}}]})
    requests = _patch_session(monkeypatch, response)
    service = AIService(
        ServerConfig(
            ai_api_key="test-key",
            ai_api_base="https://example.test/v1",
            ai_model="test-model",
        )
    )

    result = asyncio.run(service.query("ping"))

    assert result == "pong"
    assert requests[0]["url"] == "https://example.test/v1/chat/completions"
    assert requests[0]["headers"]["Authorization"] == "Bearer test-key"
    assert requests[0]["json"]["model"] == "test-model"
    assert requests[0]["json"]["messages"][-1] == {"role": "user", "content": "ping"}


def test_query_with_context_keeps_prompt_plain_and_adds_non_impersonation_guard(monkeypatch):
    response = _FakeResponse(body={"choices": [{"message": {"content": "hello"}}]})
    requests = _patch_session(monkeypatch, response)
    service = AIService(
        ServerConfig(
            ai_api_key="test-key",
            ai_api_base="https://example.test/v1",
            ai_model="test-model",
        )
    )

    result = asyncio.run(
        service.query_with_context(
            "请向观众打个招呼。",
            username="alice",
            history=[{"role": "assistant", "content": "previous"}],
        )
    )

    assert result == "hello"
    messages = requests[0]["json"]["messages"]
    assert messages[-1] == {"role": "user", "content": "请向观众打个招呼。"}
    assert any(
        msg.get("role") == "system"
        and "alice" in msg.get("content", "")
        and "AI Assistant" in msg.get("content", "")
        for msg in messages
    )


def test_query_http_error_returns_fallback(monkeypatch):
    requests = _patch_session(monkeypatch, _FakeResponse(status=401, text="bad key"))
    service = AIService(ServerConfig(ai_api_key="bad-key"))

    result = asyncio.run(service.query("ping"))

    assert requests
    assert "认证失败" in result


def test_query_timeout_returns_fallback(monkeypatch):
    _patch_session(monkeypatch, _TimeoutResponse())
    service = AIService(ServerConfig(ai_api_key="test-key"))

    result = asyncio.run(service.query("ping"))

    assert "请求超时" in result


def test_query_parse_error_returns_fallback(monkeypatch):
    bad_json = json.JSONDecodeError("bad", "{}", 0)
    _patch_session(monkeypatch, _FakeResponse(body=bad_json))
    service = AIService(ServerConfig(ai_api_key="test-key"))

    result = asyncio.run(service.query("ping"))

    assert "响应解析失败" in result
