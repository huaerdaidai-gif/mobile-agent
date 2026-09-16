# -*- coding: utf-8 -*-
"""AgentService：把 Core + Model + Host + Capability + Session 组装成一个可用的服务。

职责边界：
  - 只做「组装」和「提供 ask()」，不知道 QQ、也不知道 HTTP；
  - 一个进程一个 Service，多个会话共用同一个 ModelProvider 实例；
  - 普通请求路径就是 Agent → Model，没有额外中间层。
"""

import json
import os

import main as core_cli
from adapter.capability import register_core_tools
from adapter.model import build_provider
from adapter.platform import detect_host
from adapter.qq import build_qq_adapter
from extension.interfaces import ExtensionRegistry
from model.registry import ModelRegistry
from model.router import LightweightModelRouter
from runtime import config as runtime_config
from runtime import logging as agent_log
from runtime.scheduler import Task, build_scheduler
from runtime.session import SessionManager
from tools import registry as tool_registry

# 项目根目录（runtime/ 的上一级）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AgentService(object):
    """一次组装，全进程复用。"""

    def __init__(self, config: dict = None, provider=None, host=None, qq_adapter=None,
                 auto_probe: bool = True):
        self.config = config or runtime_config.load()
        logging_config = self.config.get("logging") or {}
        agent_log.setup(level=logging_config.get("level", "INFO"),
                        log_file=logging_config.get("file") or None)

        self.provider = provider or build_provider(self.config)
        # Stage 1：多模型注册表（primary 常驻；vision/audio 默认 UNAVAILABLE）
        self.model_registry = ModelRegistry(self.config, probe=auto_probe,
                                            primary_provider=self.provider)
        self.model_router = LightweightModelRouter(registry=self.model_registry)
        self.host = host or detect_host(runtime_config.get(self.config, "host.profile", "auto"),
                                        workdir=PROJECT_ROOT)
        self.capabilities = register_core_tools()
        self.extensions = ExtensionRegistry(
            admin_enabled=bool(runtime_config.get(self.config, "extension.admin_enabled", False)))
        self.qq_adapter = qq_adapter if qq_adapter is not None else build_qq_adapter(self.config)
        self.gateway_state = {}

        self.server_info = {"context": None, "model": None, "source": None, "error": None}
        if auto_probe and hasattr(self.provider, "detect_context"):
            self.server_info = self.provider.detect_context()
            agent_log.log("MODEL", "上下文探测完成",
                          context=self.server_info.get("context"),
                          source=self.server_info.get("source"),
                          error=self.server_info.get("error"))

        # Stage 2：按配置 + 真实能力配置工具注册表（不可用的工具绝不进 schema）
        self.tool_registry_configured = True
        tool_registry.configure(
            self.config,
            capabilities={"vision": bool(self.model_registry.vision().supports_vision()),
                          "audio": bool(self.model_registry.audio().supports_audio()),
                          "network": True, "music_provider": False},
            limits=dict(runtime_config.web_limits(self.config),
                        tool_timeout=int((self.config.get("scheduler") or {}).get("tool_timeout", 15))))

        # Stage 3：轻量调度器（模型并行默认关闭）
        self.scheduler = build_scheduler(self.config)

        gateway_config = self.config.get("gateway") or {}
        self.sessions = SessionManager(
            factory=self.new_agent,
            max_sessions=int(gateway_config.get("max_sessions", 16)),
            idle_seconds=int(gateway_config.get("idle_seconds", 3600)),
        )
        for issue in runtime_config.warnings(self.config):
            agent_log.log("RUNTIME", "配置告警：%s" % issue, level="WARNING")

    # ---------------- 核心入口 ----------------

    def new_agent(self):
        """新建一个 Agent（复用同一个 Provider 与已探测的上下文）。"""
        weather_section = (self.config.get("tools") or {}).get("weather")
        default_location = str(weather_section.get("default_location", "") or "") \
            if isinstance(weather_section, dict) else ""
        return core_cli.build_agent(self.config, llm=self.provider.llm,
                                    server_info=self.server_info, verbose=False,
                                    extra_agent_kwargs={
                                        "model_router": self.model_router,
                                        "default_location": default_location,
                                    })

    def ask(self, text: str, session_id: str = "default", on_text=None) -> str:
        """处理一句话，返回回复文本。"""
        session = self.sessions.get(session_id)
        reply = session.ask(text, on_text=on_text)
        timing = getattr(session.agent, "last_timing", None) or {}
        if timing:
            # 生产日志只保留 4 个数字；详细 timing 只在 DEBUG 打
            agent_log.log("CORE", "timing total=%sms ttft=%sms model=%sms tool=%sms"
                          % (timing.get("total_ms"), timing.get("model_ttft_ms"),
                             timing.get("model_generation_ms"), timing.get("tool_ms")))
            agent_log.log("CORE", "timing_detail %s" % json.dumps(timing, ensure_ascii=False),
                          level="DEBUG")
        return reply

    # ---------------- 状态与健康 ----------------

    def context_limit(self) -> int:
        configured = runtime_config.context_limit(self.config)
        server = self.server_info.get("context")
        return min(configured, server) if server else configured

    def model_health(self, probe: bool = True) -> dict:
        """模型健康：probe=True 时发一次轻量探测请求。"""
        if not probe:
            return {"ok": True, "checked": False, "base_url": self.provider.base_url}
        return self.provider.health()

    def host_info(self) -> dict:
        profile = self.host.profile()
        snapshot = self.host.snapshot()
        extra = self.host.capabilities() if hasattr(self.host, "capabilities") else {}
        return {"ok": True, "host": self.host.name, "profile": profile.to_dict(),
                "snapshot": snapshot.to_dict(), "capabilities": extra}

    def capability_info(self) -> dict:
        return {"ok": True, "count": len(self.capabilities.names()),
                "names": self.capabilities.names(),
                "specs": [spec.to_dict() for spec in self.capabilities.specs()]}

    def status(self) -> dict:
        """给 CLI status 用的汇总。"""
        model = self.model_health(probe=True)
        return {
            "service": "mobile-agent",
            "model": model,
            "host": self.host_info(),
            "capabilities": self.capability_info(),
            "sessions": self.sessions.stats(),
            "context": {"configured": runtime_config.context_limit(self.config),
                        "server": self.server_info.get("context"),
                        "effective": self.context_limit()},
            "gateway": dict(self.gateway_state),
            "extensions": self.extensions.health(),
        }
