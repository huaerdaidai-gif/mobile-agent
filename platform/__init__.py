# -*- coding: utf-8 -*-
"""平台相关能力（v0.2 只做 Termux 检测，不做任何重型适配）。

注意：本包与标准库的 `platform` 模块同名。因为项目根目录在 sys.path 最前面，
它会遮蔽标准库版本。为了不让第三方库（例如 requests.help 里的 `import platform`
之后调用 platform.system()）出错，这里在导入时把标准库 platform 的公开属性
桥接进来，两种用法都能正常工作。
"""

import importlib.util
import os
import sys

from .termux import battery_status, has_termux_api, is_android, is_termux, notification, wake_lock

__all__ = [
    "is_termux",
    "is_android",
    "has_termux_api",
    "wake_lock",
    "notification",
    "battery_status",
]


def _bridge_stdlib_platform() -> None:
    """把标准库 platform.py 的公开属性复制到本模块，避免同名遮蔽带来的副作用。"""
    for entry in sys.path:
        candidate = os.path.join(entry or ".", "platform.py")
        if os.path.isfile(candidate) and os.path.realpath(candidate) != os.path.realpath(__file__):
            try:
                spec = importlib.util.spec_from_file_location("_stdlib_platform", candidate)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except Exception:  # 桥接失败也不影响 Termux 检测本身
                return
            for name in dir(module):
                if not name.startswith("_") and name not in globals():
                    globals()[name] = getattr(module, name)
            return


_bridge_stdlib_platform()
