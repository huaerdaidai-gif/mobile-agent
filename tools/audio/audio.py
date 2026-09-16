# -*- coding: utf-8 -*-
"""音频工具接口（Stage 2）：只定义接口，不假装可用。

当前没有任何轻量 STT/TTS 模型，因此两个工具都返回 audio_unavailable。
未来接入后：QQ 语音 → speech_to_text → Primary → （可选）text_to_speech → QQ。
"""

UNAVAILABLE_REASON = ("audio_unavailable: 当前未部署音频模型（无 STT/TTS Provider，"
                      "llama.cpp /props modalities.audio=false）")


def speech_to_text(path_or_url: str = None, timeout: int = 30) -> dict:
    """语音转文字（未部署 → 明确返回不可用）。"""
    return {"ok": False, "error": UNAVAILABLE_REASON, "input": path_or_url}


def text_to_speech(text: str = None, path: str = None) -> dict:
    """文字转语音（未部署 → 明确返回不可用）。"""
    return {"ok": False, "error": UNAVAILABLE_REASON, "text_len": len(text or "")}


def audio_provider_status() -> dict:
    """给 /health 用：音频能力状态。"""
    return {"ok": False, "state": "UNAVAILABLE", "supports_stt": False, "supports_tts": False,
            "detail": UNAVAILABLE_REASON}


__all__ = ["speech_to_text", "text_to_speech", "audio_provider_status", "UNAVAILABLE_REASON"]
