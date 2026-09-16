# -*- coding: utf-8 -*-
"""OpenAI 兼容 Provider：直接复用 v0.25.1 的 llm.LLM，不重新实现 HTTP 逻辑。

llama.cpp / ollama / vLLM 等只要提供 /v1/chat/completions 都能用。
"""

import time

from llm import LLM, LLMError, detect_server_capabilities, detect_server_context
from model.provider import (STATE_ERROR, STATE_READY, STATE_UNAVAILABLE, ModelInfo,
                            ModelProvider)


class OpenAICompatibleProvider(ModelProvider):
    """把 llm.LLM 包装成 ModelProvider。"""

    name = "openai-compatible"
    role = "primary"
    kind = "text"

    def __init__(self, base_url: str, name: str = "", temperature: float = 0.2,
                 timeout: int = 300, max_output_tokens: int = 512,
                 role: str = "primary", kind: str = "text"):
        self.base_url = (base_url or "").rstrip("/")
        self.model_name = name
        self.max_output_tokens = int(max_output_tokens or 0)
        self.role = role
        self.kind = kind
        # 复用现有 LLM：Agent 直接使用这个实例，流式/超时/timings 行为与 v0.25.1 完全一致
        self.llm = LLM(base_url=self.base_url, model=name, temperature=temperature,
                       timeout=timeout, max_tokens=self.max_output_tokens)
        self._context = None
        self._state = STATE_UNAVAILABLE
        self._detail = "尚未探测"
        # 真实能力：默认全 False，只有探测/配置确认才置 True
        self._vision = False
        self._audio = False

    # ---- ModelProvider 接口 ----

    def model_info(self) -> ModelInfo:
        return ModelInfo(role=self.role, name=self.model_name, kind=self.kind,
                         provider=self.name, endpoint=self.base_url,
                         context=self._context, max_output_tokens=self.max_output_tokens,
                         supports_stream=True, supports_text=True,
                         supports_vision=self._vision, supports_audio=self._audio,
                         state=self._state, detail=self._detail)

    # 兼容 v0.25.1 的旧名字
    def profile(self) -> ModelInfo:
        return self.model_info()

    def supports_text(self) -> bool:
        return True

    def supports_vision(self) -> bool:
        return self._vision

    def supports_audio(self) -> bool:
        return self._audio

    def chat(self, messages, **kwargs):
        return self.llm.chat(messages, **kwargs)

    def stream(self, messages, **kwargs):
        return self.llm.chat_stream(messages, **kwargs)

    def detect_context(self) -> dict:
        """探测服务器上下文（GET /v1/models → GET /props），并缓存。"""
        info = detect_server_context(self.base_url, timeout=5)
        self._context = info.get("context")
        if self._context:
            self._state = STATE_READY
            self._detail = "上下文 %s（来源 %s）" % (self._context, info.get("source"))
        else:
            self._state = STATE_ERROR
            self._detail = info.get("error") or "无法探测上下文"
        return info

    def probe(self) -> dict:
        """真实能力探测：llama.cpp /props 的 modalities + 上下文。"""
        info = detect_server_capabilities(self.base_url, timeout=5)
        if info.get("context"):
            self._context = info["context"]
        if info.get("vision") is not None:
            self._vision = bool(info["vision"])
        if info.get("audio") is not None:
            self._audio = bool(info["audio"])
        if self._context:
            self._state = STATE_READY
        elif info.get("error"):
            self._state = STATE_ERROR
        self._detail = "context=%s vision=%s audio=%s" % (
            self._context, self._vision, self._audio)
        return dict(info, role=self.role, name=self.model_name)

    def health(self) -> dict:
        """健康检查：一次 /v1/models 探测 + 耗时。"""
        started = time.time()
        try:
            info = self.detect_context()
        except LLMError as exc:  # 理论上 detect_server_context 不抛，这里兜底
            info = {"context": None, "error": str(exc)}
        latency_ms = int((time.time() - started) * 1000)
        ok = info.get("context") is not None
        return {
            "ok": ok,
            "provider": self.name,
            "base_url": self.base_url,
            "model": self.model_name,
            "role": self.role,
            "context": info.get("context"),
            "state": self._state,
            "supports_text": True,
            "supports_vision": self._vision,
            "supports_audio": self._audio,
            "latency_ms": latency_ms,
            "error": info.get("error") if not ok else None,
        }

    def last_metrics(self) -> dict:
        """上一次请求的服务端 timings / usage（观测用）。"""
        return {"timings": dict(self.llm.last_timings or {}),
                "usage": dict(self.llm.last_usage or {})}


def build_provider(config: dict) -> OpenAICompatibleProvider:
    """按配置构造 Provider（配置来自 runtime.config.model_settings）。"""
    from runtime import config as runtime_config

    settings = runtime_config.model_settings(config)
    if settings["provider"] != "openai-compatible":
        raise ValueError("暂不支持的 model.provider：%s" % settings["provider"])
    return OpenAICompatibleProvider(
        base_url=settings["base_url"],
        name=settings["name"],
        temperature=settings["temperature"],
        timeout=settings["timeout"],
        max_output_tokens=settings["max_output_tokens"],
    )
