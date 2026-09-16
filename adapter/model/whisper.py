# -*- coding: utf-8 -*-
"""Whisper STT Provider：接 whisper.cpp 的 whisper-server（非 OpenAI 协议）。

职责严格单一（与 Agent / 音频预处理解耦）：
    WAV 文件 ──multipart POST {base_url}/inference──▶ transcript 文本

  - 不做 ffmpeg 转码：那是音频预处理层（adapter/model/audio_preprocess.py）的事；
  - 不做繁简转换、不做内容改写、失败就如实报错，绝不编造转写结果；
  - 只用标准库（urllib + 手写 multipart），不引入任何第三方依赖。

接口（不含前导斜杠，官方 server 默认 /inference）：`/inference`
支持的表单字段（已核对 whisper.cpp examples/server/server.cpp）：
    file, response_format, language, prompt, carry_initial_prompt, temperature ...
"""

import json
import os
import time
import urllib.error
import urllib.request
import uuid

from llm import LLMError
from model.provider import (STATE_ERROR, STATE_READY, STATE_UNAVAILABLE, ModelInfo,
                            ModelProvider)

# 探测失败后最短多久允许再探一次（避免每次能力查询都打网络）
REPROBE_INTERVAL_SECONDS = 5.0


class WhisperError(LLMError):
    """STT 调用失败（继承 LLMError，走项目现有错误处理链路）。"""


def build_multipart(fields, file_field, filename, content, boundary) -> bytes:
    """手写 multipart/form-data（标准库实现，不依赖 requests）。"""
    parts = []
    for key, value in fields.items():
        if value is None or value == "":
            continue
        parts.append(("--%s\r\n" % boundary).encode("utf-8"))
        parts.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % key).encode("utf-8"))
        parts.append(("%s\r\n" % value).encode("utf-8"))
    parts.append(("--%s\r\n" % boundary).encode("utf-8"))
    parts.append(('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                  % (file_field, filename)).encode("utf-8"))
    parts.append(b"Content-Type: audio/wav\r\n\r\n")
    parts.append(content)
    parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    return b"".join(parts)


def parse_transcript(body: str) -> str:
    """从 whisper-server 的 JSON 响应里取 text；取不到就是错误，不猜。"""
    try:
        data = json.loads(body)
    except ValueError:
        raise WhisperError("STT 返回内容不是 JSON：%s" % (body or "")[:120])
    if not isinstance(data, dict):
        raise WhisperError("STT 返回结构异常：%s" % str(data)[:120])
    text = data.get("text")
    if text is None:
        raise WhisperError("STT 返回里没有 text 字段：%s" % str(data)[:120])
    return str(text).strip()


class WhisperProvider(ModelProvider):
    """whisper.cpp 的 whisper-server（CPU STT）。"""

    name = "whisper-server"
    role = "audio"
    kind = "audio"

    def __init__(self, base_url: str, name: str = "whisper-base", language: str = "zh",
                 timeout: int = 120, initial_prompt: str = "", carry_initial_prompt: bool = True,
                 role: str = "audio", kind: str = "audio"):
        self.base_url = (base_url or "").rstrip("/")
        self.model_name = name or "whisper-base"
        self.language = language or ""
        self.timeout = int(timeout or 120)
        # initial prompt 只用来偏置输出（例如简体中文）；不做任何文本后处理
        self.initial_prompt = initial_prompt or ""
        self.carry_initial_prompt = bool(carry_initial_prompt)
        self.role = role
        self.kind = kind
        self._state = STATE_UNAVAILABLE
        self._detail = "尚未探测"
        self._last_probe = 0.0
        self.last_error = None

    # ---- ModelProvider 接口 ----

    def model_info(self) -> ModelInfo:
        return ModelInfo(role=self.role, name=self.model_name, kind=self.kind,
                         provider=self.name, endpoint=self.base_url,
                         supports_stream=False, supports_text=False,
                         supports_vision=False,
                         supports_audio=(self._state == STATE_READY),
                         state=self._state, detail=self._detail)

    def profile(self) -> ModelInfo:      # 兼容旧名字
        return self.model_info()

    def supports_text(self) -> bool:
        return False

    def supports_vision(self) -> bool:
        return False

    def supports_audio(self) -> bool:
        """是否可用：以最近一次 /health 探测为准；从未成功探测过就按不可用处理。"""
        if self._state != STATE_READY:
            self._maybe_reprobe()
        return self._state == STATE_READY

    def chat(self, messages, **kwargs):
        raise WhisperError("WhisperProvider 只做语音转写（transcribe），不支持 chat()")

    def stream(self, messages, **kwargs):
        raise WhisperError("WhisperProvider 只做语音转写（transcribe），不支持流式对话")

    def health(self) -> dict:
        info = self.probe()
        return {"ok": info.get("ok", False), "provider": self.name, "role": self.role,
                "base_url": self.base_url, "state": self._state, "supports_audio":
                self._state == STATE_READY, "error": info.get("error")}

    def probe(self) -> dict:
        """探测：whisper-server 没有 /props，用 GET /health（模型在启动时已加载）。"""
        self._last_probe = time.time()
        url = self.base_url + "/health"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                code = getattr(response, "status", response.getcode())
        except urllib.error.HTTPError as exc:
            self._state = STATE_ERROR
            self._detail = "HTTP %s" % exc.code
            self.last_error = self._detail
            return {"ok": False, "state": self._state, "error": self._detail}
        except Exception as exc:
            self._state = STATE_ERROR if self.base_url else STATE_UNAVAILABLE
            self._detail = str(exc)[:120]
            self.last_error = self._detail
            return {"ok": False, "state": self._state, "error": self._detail}
        self._state = STATE_READY if 200 <= int(code) < 300 else STATE_ERROR
        self._detail = "GET /health -> %s" % code
        self.last_error = None
        return {"ok": self._state == STATE_READY, "state": self._state,
                "language": self.language}

    def _maybe_reprobe(self) -> None:
        if time.time() - self._last_probe >= REPROBE_INTERVAL_SECONDS:
            self.probe()

    def last_metrics(self) -> dict:
        return {}

    # ---- STT 主接口 ----

    def transcribe(self, audio_path: str) -> str:
        """把本地 WAV 交给 whisper-server，返回转写文本（失败抛 WhisperError）。"""
        path = str(audio_path or "")
        if not path or not os.path.isfile(path):
            raise WhisperError("待转写音频不存在：%s" % (path or "(空)"))
        if not self.base_url:
            raise WhisperError("未配置 whisper-server 地址（models.audio.base_url）")
        try:
            with open(path, "rb") as handle:
                content = handle.read()
        except OSError as exc:
            raise WhisperError("读取音频失败：%s" % exc)
        if not content:
            raise WhisperError("音频文件为空：%s" % os.path.basename(path))

        boundary = "----mobileagent%s" % uuid.uuid4().hex
        fields = {"response_format": "json"}
        if self.language:
            fields["language"] = self.language
        if self.initial_prompt:
            fields["prompt"] = self.initial_prompt
            fields["carry_initial_prompt"] = "true" if self.carry_initial_prompt else "false"
        body = build_multipart(fields, "file", os.path.basename(path), content, boundary)
        request = urllib.request.Request(
            self.base_url + "/inference", data=body, method="POST",
            headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary,
                     "Content-Length": str(len(body))})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:120]
            except Exception:
                pass
            self.last_error = "HTTP %s %s" % (exc.code, detail)
            raise WhisperError("STT 服务返回 %s：%s" % (exc.code, detail or "无响应体"))
        except Exception as exc:
            self.last_error = str(exc)[:120]
            raise WhisperError("无法连接 STT 服务（%s）：%s" % (self.base_url, exc))
        return parse_transcript(raw)


__all__ = ["WhisperProvider", "WhisperError", "build_multipart", "parse_transcript",
           "REPROBE_INTERVAL_SECONDS"]
