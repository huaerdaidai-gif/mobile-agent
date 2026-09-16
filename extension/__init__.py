# -*- coding: utf-8 -*-
"""Extension 层：只留接口，不实现具体功能。

未来要接的东西（学习、设备注册、参数调优、插件）都在这里定义抽象，
默认全部关闭；启用与访问控制由 gateway 的认证/授权/策略链负责。
"""

from extension.interfaces import (
    DeviceAuthorization,
    DeviceRegistry,
    ExtensionRegistry,
    LearningEngine,
    ParameterTuner,
    Plugin,
    PolicyGate,
)

__all__ = [
    "LearningEngine",
    "DeviceRegistry",
    "DeviceAuthorization",
    "ParameterTuner",
    "Plugin",
    "ExtensionRegistry",
    "PolicyGate",
]
