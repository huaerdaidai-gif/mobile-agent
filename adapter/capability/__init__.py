# -*- coding: utf-8 -*-
"""能力适配：把现有 Tool 系统包装成 Capability。"""

from adapter.capability.tool_capability import ToolCapability, register_core_tools

__all__ = ["ToolCapability", "register_core_tools"]
