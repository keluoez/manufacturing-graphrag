import hashlib
import json
import random
import time
from typing import Any

from . import db
from .config import settings


class LLMError(RuntimeError):
    pass


# 可重试的临时故障：429 限流 / 5xx 服务端 / 连接与超时
_RETRY_STATUS = (429, 500, 502, 503, 504)
_RETRY_HINTS = ("429", "500", "502", "503", "504", "rate limit", "timeout",
                "timed out", "connection", "temporarily", "overloaded", "busy")
_RETRY_WAIT = (2, 5, 10, 20, 30)


def _is_retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    try:
        if status is not None and int(status) in _RETRY_STATUS:
            return True
    except (TypeError, ValueError):
        pass
    msg = str(exc).lower()
    return any(h in msg for h in _RETRY_HINTS)


class LLMClient:
    def __init__(self) -> None:
        self._client = None

    @property
    def is_mock(self) -> bool:
        return settings.llm_provider == "mock"

    def _openai_client(self):
        if self._client is None:
            from openai import OpenAI

            if not settings.llm_api_key or settings.llm_api_key.startswith("sk-在此"):
                raise LLMError("未配置 LLM_API_KEY，请复制 .env.example 为 .env 并填入 DeepSeek 密钥")
            self._client = OpenAI(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                timeout=300,
            )
        return self._client

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
        json_mode: bool = False,
        max_tokens_override: int | None = None,
    ) -> str:
        if self.is_mock:
            raise LLMError("当前 LLM_PROVIDER=mock，应使用本地规则逻辑而不是真实 LLM 调用")
        cache_key = hashlib.sha256(
            json.dumps([settings.llm_provider, settings.llm_model, messages,
                        max_tokens_override],
                       ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        if settings.llm_cache_enabled:
            cached = db.cache_get(cache_key)
            if cached is not None:
                return cached

        kwargs: dict[str, Any] = {"temperature": temperature}
        if max_tokens_override:
            kwargs["max_tokens"] = max_tokens_override
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if settings.llm_disable_thinking and (
            "deepseek-v4" in settings.llm_model or settings.llm_model.startswith("glm")
        ):
            # deepseek-v4 / 智谱 GLM 推理模型：关闭思考模式（否则思考占满输出返回空）
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        last_exc: Exception | None = None
        for attempt in range(len(_RETRY_WAIT) + 1):
            try:
                resp = self._openai_client().chat.completions.create(
                    model=settings.llm_model,
                    messages=messages,
                    **kwargs,
                )
                text = resp.choices[0].message.content or ""
                break
            except Exception as exc:  # noqa: BLE001 - 重试后抛出由上层记录失败文档
                last_exc = exc
                if attempt >= len(_RETRY_WAIT) or not _is_retryable(exc):
                    raise
                wait = _RETRY_WAIT[attempt] + random.uniform(0, 2)
                time.sleep(wait)
        else:  # 理论不可达，保险
            raise LLMError(f"LLM 调用失败：{last_exc}") from last_exc
        if settings.llm_cache_enabled:
            db.cache_put(cache_key, text)
        return text

    def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
    ) -> dict | list:
        raw = self.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            json_mode=True,
        )
        return json.loads(self._repair_json(raw))

    @staticmethod
    def _repair_json(raw: str) -> str:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1] if "```" in raw[3:] else raw
            if raw.startswith("json"):
                raw = raw[4:]
        return raw.strip()


llm = LLMClient()
