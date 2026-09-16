# -*- coding: utf-8 -*-
"""Tool → Capability 适配器。

刻意「只做包装」：
  - 工具定义仍然来自 tools/registry.py；
  - 执行仍然调用 tools/*.py 里的原函数（与 Agent 的工具循环用的是同一批函数）；
  - 校验仍然用 registry.validate_arguments。
Agent 自己的工具循环保持不变，这里只是给未来的调度器/插件系统留一个统一入口。
"""

from capability.base import Capability, CapabilitySpec, CapabilityRegistry
from tools import file as file_tool
from tools import registry as tool_registry
from tools import shell as shell_tool
from tools.time import get_current_time


class ToolCapability(Capability):
    """把单个工具包装成能力。"""

    def __init__(self, name: str, timeout: int = 15):
        self.tool_name = name
        self.timeout = timeout

    def spec(self) -> CapabilitySpec:
        spec = tool_registry.get_tool(self.tool_name) or {}
        return CapabilitySpec(
            name=self.tool_name,
            description=spec.get("description", ""),
            parameters=dict(spec.get("parameters") or {}),
            required=list(spec.get("required") or []),
            dangerous=self.tool_name in ("shell",),  # shell 属于高风险能力
        )

    def invoke(self, arguments) -> dict:
        ok, error = tool_registry.validate_arguments(self.tool_name, arguments)
        if not ok:
            return {"ok": False, "error": error}
        if self.tool_name == "time":
            raw = get_current_time()
        elif self.tool_name == "shell":
            raw = shell_tool.run_command(str(arguments.get("command", "")), timeout=self.timeout)
        elif self.tool_name == "file":
            action = str(arguments.get("action", "")).strip().lower()
            if action == "write":
                raw = file_tool.write_file(str(arguments.get("path", "")),
                                           str(arguments.get("content", "")))
            else:
                raw = file_tool.read_file(str(arguments.get("path", "")))
        else:
            return {"ok": False, "error": "没有实现的能力：%s" % self.tool_name}
        if not isinstance(raw, dict) or not raw.get("ok"):
            return {"ok": False, "error": str((raw or {}).get("error") or "执行失败")}
        return {"ok": True, "result": raw}


def register_core_tools(registry: CapabilityRegistry = None, timeout: int = 15) -> CapabilityRegistry:
    """把现有全部工具注册成能力。"""
    registry = registry or CapabilityRegistry()
    for name in tool_registry.tool_names():
        registry.register(ToolCapability(name, timeout=timeout))
    return registry
