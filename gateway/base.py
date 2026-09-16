# -*- coding: utf-8 -*-
"""Gateway 抽象：统一 request / response / session / streaming / 认证。

Agent Core 只认识「文本进、文本出」，不认识 QQ、HTTP、Telegram。
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass
class ChannelMessage:
    """一条来自外部渠道的消息。"""

    channel: str = "unknown"          # qq / cli / api ...
    session_id: str = "default"       # 会话标识（决定用哪个 Agent 上下文）
    user_id: str = ""                 # 发送者
    text: str = ""
    raw: Dict = field(default_factory=dict)
    reply_to: Dict = field(default_factory=dict)  # 回复目标（群/私聊 id 等）
    # PHASE 2：附件（{"images": [...], "audios": [...]}），默认空字典，兼容旧的构造方式
    attachments: Dict[str, List[str]] = field(default_factory=dict)


class GatewayAdapter(object):
    """渠道适配器接口：把某个渠道的事件变成 ChannelMessage，并把回复发回去。"""

    name = "adapter"

    def handle(self, payload: Dict, service: "object",
               on_text: Optional[Callable[[str], None]] = None) -> Optional[str]:
        raise NotImplementedError

    def health(self) -> Dict:
        return {"ok": True, "adapter": self.name}


class AuthProvider(object):
    """认证接口：返回 (是否通过, 身份信息)。"""

    name = "none"

    def authorize(self, headers: Dict) -> tuple:
        raise NotImplementedError


class NoAuth(AuthProvider):
    """不做认证（仅监听 127.0.0.1 时可用）。"""

    name = "none"

    def authorize(self, headers: Dict) -> tuple:
        return True, {"identity": "anonymous"}


class TokenAuth(AuthProvider):
    """简单 Token 认证：Authorization: Bearer <token> 或 X-Auth-Token。"""

    name = "token"

    def __init__(self, token: str):
        self.token = token or ""

    def authorize(self, headers: Dict) -> tuple:
        if not self.token:
            return False, {"error": "服务端未配置 token"}
        supplied = ""
        for key, value in (headers or {}).items():
            lowered = key.lower()
            if lowered == "authorization" and str(value).lower().startswith("bearer "):
                supplied = str(value)[7:].strip()
            elif lowered == "x-auth-token":
                supplied = str(value).strip()
        if supplied and supplied == self.token:
            return True, {"identity": "token-user"}
        return False, {"error": "认证失败"}


def build_auth(config: dict) -> AuthProvider:
    """按配置构造认证器。"""
    mode = str((config.get("gateway") or {}).get("auth", "none")).lower()
    if mode == "token":
        return TokenAuth((config.get("gateway") or {}).get("token", ""))
    return NoAuth()
