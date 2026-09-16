# -*- coding: utf-8 -*-
"""模型适配：把具体协议接到 ModelProvider 上。"""

from adapter.model.openai_compatible import OpenAICompatibleProvider, build_provider
from adapter.model.unavailable import ModelUnavailableError, UnavailableProvider

__all__ = ["OpenAICompatibleProvider", "build_provider",
           "UnavailableProvider", "ModelUnavailableError"]
