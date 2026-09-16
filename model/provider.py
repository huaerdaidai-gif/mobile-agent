# -*- coding: utf-8 -*-
"""ModelProvider 统一接口（Stage 1）。

设计原则：
  - 不重写 llm.py：底层 HTTP/SSE 传输仍然是 llm.LLM；
  - 能力必须真实：supports_vision/audio 由「配置 + llama.cpp /props + /v1/models」共同决定，
    探测不到就是 False，绝不伪装；
  - 生命周期用轻量字符串状态，不做模型进程管理（那是 llama.cpp 的职责）。
"""

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

# 轻量模型状态（不做后台服务，只做标记）
STATE_UNAVAILABLE = "UNAVAILABLE"
STATE_LOADING = "LOADING"
STATE_READY = "READY"
STATE_BUSY = "BUSY"
STATE_ERROR = "ERROR"


@dataclass
class ModelInfo:
    """模型描述。"""

    role: str = "primary"                  # primary / vision / audio
    name: str = ""
    kind: str = "text"                     # text / vision / audio
    provider: str = "openai-compatible"
    endpoint: str = ""
    context: Optional[int] = None
    max_output_tokens: int = 0
    supports_stream: bool = True
    supports_text: bool = True
    supports_vision: bool = False
    supports_audio: bool = False
    state: str = STATE_UNAVAILABLE
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "role": self.role, "name": self.name, "kind": self.kind,
            "provider": self.provider, "endpoint": self.endpoint,
            "context": self.context, "max_output_tokens": self.max_output_tokens,
            "supports_stream": self.supports_stream, "supports_text": self.supports_text,
            "supports_vision": self.supports_vision, "supports_audio": self.supports_audio,
            "state": self.state, "detail": self.detail,
        }


# 兼容 v0.25.1 里已经存在的名字
ModelProfile = ModelInfo


class ModelProvider(object):
    """模型提供方接口。"""

    name = "model"
    role = "primary"

    # ---- 基本信息与能力 ----

    def model_info(self) -> ModelInfo:
        raise NotImplementedError

    def state(self) -> str:
        return self.model_info().state

    def supports_text(self) -> bool:
        return self.model_info().supports_text

    def supports_vision(self) -> bool:
        return self.model_info().supports_vision

    def supports_audio(self) -> bool:
        return self.model_info().supports_audio

    # ---- 推理 ----

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        raise NotImplementedError

    def stream(self, messages: List[Dict[str, str]], **kwargs) -> Iterator[str]:
        raise NotImplementedError

    # ---- 运维 ----

    def health(self) -> Dict:
        raise NotImplementedError

    def probe(self) -> Dict:
        """可选：探测服务端能力（llama.cpp /props、/v1/models）。"""
        return {}

    def last_metrics(self) -> Dict:
        return {}
