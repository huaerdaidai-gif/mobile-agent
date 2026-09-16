# -*- coding: utf-8 -*-
"""能力不可用的模型占位实现（Vision / Audio 未部署时使用）。

原则：绝不伪装成可用。任何推理调用都会抛出 ModelUnavailableError，
由 Agent/Router 在调用前就用 supports_* / state 判断并给出人类可读说明。
"""

from typing import Dict, Iterator, List

from llm import LLMError
from model.provider import STATE_UNAVAILABLE, ModelInfo, ModelProvider


class ModelUnavailableError(LLMError):
    """模型不可用（继承 LLMError，走现有错误处理链路）。"""


class UnavailableProvider(ModelProvider):
    """不可用模型：只提供状态与说明。"""

    def __init__(self, role: str = "vision", kind: str = "vision",
                 endpoint: str = "", reason: str = "未部署（未安装模型）"):
        self.role = role
        self.kind = kind
        self.name = "%s-unavailable" % role
        self.endpoint = endpoint
        self.reason = reason

    def model_info(self) -> ModelInfo:
        return ModelInfo(role=self.role, name="", kind=self.kind, provider="none",
                         endpoint=self.endpoint, supports_stream=False,
                         supports_text=False, supports_vision=False, supports_audio=False,
                         state=STATE_UNAVAILABLE, detail=self.reason)

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        raise ModelUnavailableError("%s 模型不可用：%s" % (self.role, self.reason))

    def stream(self, messages: List[Dict[str, str]], **kwargs) -> Iterator[str]:
        raise ModelUnavailableError("%s 模型不可用：%s" % (self.role, self.reason))

    def health(self) -> Dict:
        return {"ok": False, "role": self.role, "state": STATE_UNAVAILABLE,
                "error": self.reason, "available": False}
