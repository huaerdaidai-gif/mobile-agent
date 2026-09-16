# -*- coding: utf-8 -*-
"""ModelRegistry：管理 primary / vision / audio 三类模型（Stage 1）。

能力来源（按可信度）：
  1. 显式配置（config.models.<role>.enabled / endpoint / name）
  2. llama.cpp /props 的 modalities（真实服务器能力）
  → 都没确认就是 UNAVAILABLE，绝不伪装。

本阶段不下载任何模型、不启动第二个 llama-server。
"""

from typing import Dict, List

from adapter.model.openai_compatible import OpenAICompatibleProvider
from adapter.model.unavailable import UnavailableProvider
from runtime import config as runtime_config
from runtime import logging as agent_log

ROLES = ("primary", "vision", "audio")


class ModelRegistry(object):
    """模型注册表：按角色取 Provider。"""

    def __init__(self, config: dict, probe: bool = True, primary_provider=None):
        self.config = config or {}
        self.providers: Dict[str, object] = {}
        self._build_primary(primary_provider)
        self._build_optional("vision", "vision")
        self._build_optional("audio", "audio")
        if probe:
            self.probe_all()

    # ---- 构造 ----

    def _build_primary(self, primary_provider=None) -> None:
        if primary_provider is not None:
            self.providers["primary"] = primary_provider
            return
        settings = runtime_config.model_settings(self.config)
        self.providers["primary"] = OpenAICompatibleProvider(
            base_url=settings["base_url"], name=settings["name"],
            temperature=settings["temperature"], timeout=settings["timeout"],
            max_output_tokens=settings["max_output_tokens"], role="primary", kind="text")

    def _build_optional(self, role: str, kind: str) -> None:
        """vision/audio：只有配置明确 enabled + endpoint + name 才构造真实 Provider。"""
        section = (self.config.get("models") or {}).get(role) or {}
        enabled = bool(section.get("enabled", False))
        endpoint = str(section.get("endpoint", "") or "")
        name = str(section.get("name", "") or "")
        if enabled and endpoint and name:
            self.providers[role] = OpenAICompatibleProvider(
                base_url=endpoint, name=name,
                timeout=int(section.get("timeout", 120) or 120),
                max_output_tokens=int(section.get("max_output_tokens", 256) or 256),
                role=role, kind=kind)
            agent_log.log("MODEL", "%s 模型已配置" % role, endpoint=endpoint, name=name)
            return
        reason = "未启用（config.models.%s.enabled=false）" % role if not enabled \
            else "缺少 endpoint 或 name"
        self.providers[role] = UnavailableProvider(role=role, kind=kind,
                                                   endpoint=endpoint, reason=reason)

    # ---- 查询 ----

    def get(self, role: str):
        return self.providers.get(role) or UnavailableProvider(role=role)

    def primary(self):
        return self.get("primary")

    def vision(self):
        return self.get("vision")

    def audio(self):
        return self.get("audio")

    def list_models(self) -> List[dict]:
        return [self.providers[role].model_info().to_dict() for role in ROLES
                if role in self.providers]

    def states(self) -> Dict[str, str]:
        return {role: provider.model_info().state for role, provider in self.providers.items()}

    def capabilities(self) -> Dict[str, dict]:
        """每个角色真实支持什么（给 Router / 健康检查用）。"""
        return {role: {"supports_text": provider.supports_text(),
                       "supports_vision": provider.supports_vision(),
                       "supports_audio": provider.supports_audio(),
                       "state": provider.model_info().state}
                for role, provider in self.providers.items()}

    # ---- 探测与健康 ----

    def probe_all(self) -> Dict[str, dict]:
        """真实能力探测：对支持 probe 的 Provider 调 /props。"""
        result = {}
        for role, provider in self.providers.items():
            if hasattr(provider, "probe"):
                try:
                    result[role] = provider.probe()
                except Exception as exc:  # 探测失败不影响启动
                    result[role] = {"error": str(exc)[:120]}
            else:
                result[role] = provider.model_info().to_dict()
        agent_log.log("MODEL", "模型状态 primary=%s vision=%s audio=%s"
                      % (self.providers["primary"].model_info().state,
                         self.providers["vision"].model_info().state,
                         self.providers["audio"].model_info().state), level="DEBUG")
        return result

    def health(self) -> dict:
        entries = {}
        for role, provider in self.providers.items():
            try:
                entries[role] = provider.health()
            except Exception as exc:
                entries[role] = {"ok": False, "role": role, "error": str(exc)[:160]}
        return {"ok": bool(entries.get("primary", {}).get("ok")), "models": entries,
                "capabilities": self.capabilities()}
