# -*- coding: utf-8 -*-
"""Capability / CapabilityRegistry：为将来接插件、调度器留的最小接口。

第一版只用来把现有 Tool 系统「包装成能力」，不引入 Plugin/MCP。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class CapabilitySpec:
    """能力描述。"""

    name: str = ""
    description: str = ""
    parameters: Dict[str, str] = field(default_factory=dict)
    required: List[str] = field(default_factory=list)
    dangerous: bool = False          # 高风险能力默认需要策略允许

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": dict(self.parameters),
            "required": list(self.required),
            "dangerous": self.dangerous,
        }


class Capability(object):
    """能力接口：invoke 必须返回 {"ok": bool, "result"/"error": ...}，不抛异常。"""

    def spec(self) -> CapabilitySpec:
        raise NotImplementedError

    def invoke(self, arguments: Dict) -> Dict:
        raise NotImplementedError


class CapabilityRegistry(object):
    """能力注册表。"""

    def __init__(self):
        self._items: Dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        spec = capability.spec()
        self._items[spec.name] = capability

    def get(self, name: str) -> Optional[Capability]:
        return self._items.get(name)

    def names(self) -> List[str]:
        return list(self._items.keys())

    def specs(self) -> List[CapabilitySpec]:
        return [item.spec() for item in self._items.values()]

    def invoke(self, name: str, arguments: Dict) -> Dict:
        capability = self._items.get(name)
        if capability is None:
            return {"ok": False, "error": "未知能力：%s（可用：%s）" % (name, "、".join(self.names()))}
        try:
            return capability.invoke(arguments or {})
        except Exception as exc:  # 能力实现不允许把异常抛给调用方
            return {"ok": False, "error": "能力执行失败：%s" % exc}
