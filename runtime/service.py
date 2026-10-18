# -*- coding: utf-8 -*-
"""AgentService：把 Core + Model + Host + Capability + Session 组装成一个可用的服务。

职责边界：
  - 只做「组装」和「提供 ask()」，不知道 QQ、也不知道 HTTP；
  - 一个进程一个 Service，多个会话共用同一个 ModelProvider 实例；
  - 普通请求路径就是 Agent → Model，没有额外中间层。
"""

import json
import copy
import hashlib
import os
import re

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

        self.workspace_mode = bool((self.config.get("workspace") or {}).get("enabled", False))
        self.workspace_roots = []
        # Workspace mode is deliberately a locked-down profile, not a prompt convention.
        if self.workspace_mode:
            gateway_config = self.config.get("gateway") or {}
            if (str(gateway_config.get("host", "127.0.0.1")) not in {"127.0.0.1", "localhost", "::1"}
                    or str(gateway_config.get("auth", "none")).lower() != "token"
                    or not str(gateway_config.get("token") or "").strip()):
                raise ValueError("workspace mode requires loopback binding and gateway token authentication")
            workspace_config = self.config.get("workspace") or {}
            roots = list(workspace_config.get("allowed_roots") or [])
            if not roots and workspace_config.get("allowed_root"):
                roots = [workspace_config.get("allowed_root")]
            for value in roots:
                if not isinstance(value, str) or not os.path.isabs(os.path.expanduser(value)):
                    raise ValueError("workspace allowed_root must be an absolute path")
                root = os.path.abspath(os.path.expanduser(value))
                if os.path.islink(root) or not os.path.isdir(root):
                    raise ValueError("workspace allowed_root is unavailable")
                self.workspace_roots.append(os.path.realpath(root))
            if not self.workspace_roots:
                raise ValueError("workspace mode requires an explicit allowed_root")
            tools_config = self.config.setdefault("tools", {})
            tools_config.update({"enabled": True, "allowed": ["file"], "file_read_only": True,
                                 "core": {"enabled": False}, "system": {"enabled": True},
                                 "web": {"enabled": False}, "writing": {"enabled": False},
                                 "vision": {"enabled": False}, "audio": {"enabled": False},
                                 "music": {"enabled": False}})

        # Stage 2：按配置 + 真实能力配置工具注册表（不可用的工具绝不进 schema）
        self.tool_registry_configured = True
        tool_registry.configure(
            self.config,
            capabilities={"vision": bool(self.model_registry.vision().supports_vision()),
                          "audio": bool(self.model_registry.audio().supports_audio()),
                          "network": not self.workspace_mode, "music_provider": False},
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

    def new_agent(self, project_id: str = None):
        """新建一个 Agent（复用同一个 Provider 与已探测的上下文）。"""
        weather_section = (self.config.get("tools") or {}).get("weather")
        default_location = str(weather_section.get("default_location", "") or "") \
            if isinstance(weather_section, dict) else ""
        config = self.config
        if self.workspace_mode and project_id:
            config = copy.deepcopy(self.config)
            workspace = config.get("workspace") or {}
            memory_dir = str(workspace.get("memory_dir") or
                             os.path.join(PROJECT_ROOT, "memory", "projects"))
            project_key = hashlib.sha256(project_id.encode("utf-8")).hexdigest()
            config.setdefault("memory", {})["path"] = os.path.join(memory_dir, project_key + ".json")
        return core_cli.build_agent(config, llm=self.provider.llm,
                                    server_info=self.server_info, verbose=False,
                                    extra_agent_kwargs={
                                        "model_router": self.model_router,
                                        "default_location": default_location,
                                    })

    def ask(self, text: str, session_id: str = "default", on_text=None,
            attachments: dict = None, project_id: str = None, project_root: str = None) -> str:
        """处理一句话，返回回复文本。attachments（图片/音频）只透传给 Agent。"""
        tool_context = None
        if self.workspace_mode:
            project_id, project_root = self._workspace_context(project_id, project_root)
            session_id = "workspace:" + hashlib.sha256(
                (project_id + "\0" + str(session_id)).encode("utf-8")).hexdigest()
            tool_context = {"project_root": project_root}
            factory = lambda: self.new_agent(project_id=project_id)
        else:
            factory = None
        session = self.sessions.get(session_id, factory=factory)
        reply = session.ask(text, on_text=on_text, attachments=attachments,
                            tool_context=tool_context)
        timing = getattr(session.agent, "last_timing", None) or {}
        if timing:
            # 生产日志只保留 4 个数字；详细 timing 只在 DEBUG 打
            agent_log.log("CORE", "timing total=%sms ttft=%sms model=%sms tool=%sms"
                          % (timing.get("total_ms"), timing.get("model_ttft_ms"),
                             timing.get("model_generation_ms"), timing.get("tool_ms")))
            agent_log.log("CORE", "timing_detail %s" % json.dumps(timing, ensure_ascii=False),
                          level="DEBUG")
        return reply

    def _workspace_context(self, project_id, project_root):
        if not isinstance(project_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", project_id):
            raise ValueError("workspace project_id is invalid")
        if not isinstance(project_root, str) or not os.path.isabs(project_root):
            raise ValueError("workspace project_root must be an absolute path")
        candidate = os.path.abspath(project_root)
        if os.path.islink(candidate) or not os.path.isdir(candidate):
            raise ValueError("workspace project_root is unavailable")
        resolved = os.path.realpath(candidate)
        if not any(resolved == allowed or resolved.startswith(allowed + os.sep)
                   for allowed in self.workspace_roots):
            raise ValueError("workspace project_root is outside configured roots")
        return project_id, resolved

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
