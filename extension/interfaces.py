# -*- coding: utf-8 -*-
"""未来能力的抽象接口（本轮不实现具体逻辑）。

安全链路（未来所有远程控制都必须走这条链）：

    Authentication → Authorization → Policy → Executor

  - Authentication：gateway.auth（TokenAuth / NoAuth）
  - Authorization：identity 能不能碰这个 session / 设备
  - Policy：PolicyGate 判断这个动作是否被允许（默认拒绝高风险动作）
  - Executor：真正执行（当前只有 Agent 与 Capability）
"""

from typing import Dict, List, Optional


class LearningEngine(object):
    """学习引擎：从对话里沉淀规则/偏好。当前不实现。"""

    name = "learning"

    def observe(self, session_id: str, user_text: str, reply_text: str) -> None:
        raise NotImplementedError

    def suggest(self, session_id: str) -> List[str]:
        raise NotImplementedError


class DeviceRegistry(object):
    """设备注册表：记录有哪些设备、各自的能力。当前不实现。"""

    name = "devices"

    def list_devices(self) -> List[Dict]:
        raise NotImplementedError

    def get(self, device_id: str) -> Optional[Dict]:
        raise NotImplementedError


class DeviceAuthorization(object):
    """设备授权：某设备是否允许被控制。当前不实现。"""

    name = "device-authz"

    def is_authorized(self, device_id: str, action: str) -> bool:
        raise NotImplementedError


class ParameterTuner(object):
    """参数调优：按资源情况调整模型参数。当前不实现。"""

    name = "tuner"

    def tune(self, profile: Dict, snapshot: Dict) -> Dict:
        raise NotImplementedError


class Plugin(object):
    """插件接口：未来扩展能力的统一入口（当前只有内置 Tool 能力）。"""

    name = "plugin"

    def register(self, capability_registry) -> None:
        raise NotImplementedError

    def health(self) -> Dict:
        return {"ok": True, "name": self.name}


class PolicyGate(object):
    """策略门：默认关闭高风险动作，只有显式开启才放行。"""

    def __init__(self, admin_enabled: bool = False, dangerous_allowed: bool = False):
        self.admin_enabled = bool(admin_enabled)
        self.dangerous_allowed = bool(dangerous_allowed)

    def allow(self, action: str, identity: Dict = None) -> Dict:
        """返回 {"allowed": bool, "reason": str}。"""
        if action.startswith("admin."):
            if not self.admin_enabled:
                return {"allowed": False, "reason": "管理接口默认关闭（extension.admin_enabled=false）"}
            return {"allowed": True, "reason": "admin enabled"}
        if action.startswith("dangerous.") and not self.dangerous_allowed:
            return {"allowed": False, "reason": "高风险动作默认关闭"}
        return {"allowed": True, "reason": "default allow"}


class ExtensionRegistry(object):
    """扩展注册表：默认空实现，全部返回「未启用」。"""

    def __init__(self, admin_enabled: bool = False):
        self.policy = PolicyGate(admin_enabled=admin_enabled)
        self._items: Dict[str, object] = {}

    def register(self, extension) -> None:
        self._items[getattr(extension, "name", extension.__class__.__name__)] = extension

    def get(self, name: str):
        return self._items.get(name)

    def names(self) -> List[str]:
        return list(self._items.keys())

    def health(self) -> Dict:
        return {"ok": True, "enabled": self.names(), "admin_enabled": self.policy.admin_enabled}
