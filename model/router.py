# -*- coding: utf-8 -*-
"""轻量模型 Router（Stage 1）。

原则：
  - 不使用第二个 LLM 判断，纯确定性规则（零 token、零延迟）；
  - 普通聊天永远只走 primary；
  - 需要视觉/音频时先检查真实可用性，不可用就如实降级（交给 primary 解释）；
  - 「同时存在」不等于「同时推理」：需要多步时返回 steps 列表，由调度器串行执行。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

ROUTE_TEXT = "TEXT"
ROUTE_VISION = "VISION"
ROUTE_AUDIO = "AUDIO"
ROUTE_VISION_TO_TEXT = "VISION_TO_TEXT"
ROUTE_AUDIO_TO_TEXT = "AUDIO_TO_TEXT"
ROUTE_MULTIMODAL = "MULTIMODAL"

# 触发词（保守：宁可漏，不要误触发普通聊天）
_VISION_WORDS = ("这张图", "图片里", "图片内容", "图片中", "看图", "识别图片", "看图说话",
                 "截图", "图中", "ocr", "文字识别", "识别一下这张", "这张照片", "照片里")
_AUDIO_WORDS = ("语音", "录音", "听一下", "转成文字", "转文字", "语音转文字", "朗读",
                "念一下", "这段音频")
# 需要「先理解，再推理/工具」的复杂意图
_REASONING_WORDS = ("推理", "分析", "为什么", "怎么办", "怎么操作", "步骤", "教程",
                    "然后", "帮我做", "帮我处理", "总结", "翻译")


@dataclass
class ModelRoute:
    """路由结果。"""

    route: str = ROUTE_TEXT
    steps: List[str] = field(default_factory=list)
    reason: str = ""
    degraded: bool = False
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"route": self.route, "steps": list(self.steps), "reason": self.reason,
                "degraded": self.degraded, "notes": list(self.notes)}


class LightweightModelRouter(object):
    """确定性路由器：看文本 / 附件 / 能力，决定用哪个模型。"""

    def __init__(self, registry=None, vision_available: bool = False,
                 audio_available: bool = False):
        self.registry = registry
        self._vision_available = bool(vision_available)
        self._audio_available = bool(audio_available)

    def vision_available(self) -> bool:
        """优先问 registry（真实能力），没有 registry 时用显式开关。"""
        if self.registry is not None:
            try:
                return bool(self.registry.get("vision").supports_vision())
            except Exception:
                return False
        return self._vision_available

    def audio_available(self) -> bool:
        if self.registry is not None:
            try:
                return bool(self.registry.get("audio").supports_audio())
            except Exception:
                return False
        return self._audio_available

    def route(self, text: str = "", attachments: Optional[Dict] = None,
              tool_intent: Optional[List[str]] = None) -> ModelRoute:
        """返回 ModelRoute。attachments 形如 {"images": [...], "audios": [...]}。"""
        lowered = (text or "").lower()
        attachments = attachments or {}
        has_image = bool(attachments.get("images")) or self._mentions(lowered, _VISION_WORDS)
        has_audio = bool(attachments.get("audios")) or self._mentions(lowered, _AUDIO_WORDS)
        needs_reasoning = self._mentions(lowered, _REASONING_WORDS) or bool(tool_intent)

        if has_image and has_audio:
            if self.vision_available() and self.audio_available():
                return ModelRoute(route=ROUTE_MULTIMODAL,
                                  steps=["vision", "audio", "primary"],
                                  reason="同时包含图片与音频")
            missing = "vision" if not self.vision_available() else "audio"
            return self._degrade(missing, "需要视觉与音频模型，但 %s = UNAVAILABLE" % missing)
        if has_image:
            if self.vision_available():
                if needs_reasoning:
                    return ModelRoute(route=ROUTE_VISION_TO_TEXT, steps=["vision", "primary"],
                                      reason="图片 + 需要推理/工具")
                return ModelRoute(route=ROUTE_VISION, steps=["vision"], reason="只需要理解图片")
            return self._degrade("vision", "需要视觉模型，但当前 vision = UNAVAILABLE")
        if has_audio:
            if self.audio_available():
                if needs_reasoning:
                    return ModelRoute(route=ROUTE_AUDIO_TO_TEXT, steps=["audio", "primary"],
                                      reason="音频 + 需要推理/工具")
                return ModelRoute(route=ROUTE_AUDIO, steps=["audio"], reason="只需要转写音频")
            return self._degrade("audio", "需要音频模型，但当前 audio = UNAVAILABLE")
        return ModelRoute(route=ROUTE_TEXT, steps=["primary"], reason="纯文本，只走主模型")

    @staticmethod
    def _mentions(text: str, words) -> bool:
        return any(word in text for word in words)

    def _degrade(self, missing: str, reason: str) -> ModelRoute:
        """模型不可用时的降级：仍然走 primary，但带上说明让模型如实告知用户。"""
        label = "视觉" if missing == "vision" else "音频"
        return ModelRoute(route=ROUTE_TEXT, steps=["primary"], reason=reason, degraded=True,
                          notes=["%s模型不可用（UNAVAILABLE），请如实告知用户，不要假装已完成" % label])
