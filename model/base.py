# -*- coding: utf-8 -*-
"""兼容层：真正的接口定义在 model/provider.py。

v0.25.1 的调用方（adapter/model/openai_compatible.py、测试）从这里导入，
这里统一转出去，避免重复定义两套接口。
"""

from model.provider import (  # noqa: F401
    STATE_BUSY,
    STATE_ERROR,
    STATE_LOADING,
    STATE_READY,
    STATE_UNAVAILABLE,
    ModelInfo,
    ModelProfile,
    ModelProvider,
)

__all__ = ["ModelInfo", "ModelProfile", "ModelProvider", "STATE_UNAVAILABLE",
           "STATE_LOADING", "STATE_READY", "STATE_BUSY", "STATE_ERROR"]
