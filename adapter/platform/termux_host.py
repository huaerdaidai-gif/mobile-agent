# -*- coding: utf-8 -*-
"""Termux（Android）Host 适配器。

这里才允许出现 Termux / Android 相关判断；Core 不感知。
复用 v0.2 就有的 platform/termux.py（is_termux / has_termux_api / battery_status）。
"""

from adapter.platform.generic_host import GenericHost
from host.base import HostProfile, ResourceSnapshot
from platform import is_termux as _is_termux
from platform import has_termux_api, battery_status


class TermuxHost(GenericHost):
    """跑在 Termux 里的 Host：额外提供温度、电池、唤醒锁能力。"""

    name = "termux"
    platform_name = "termux"
    backends = ("cpu", "llama.cpp", "termux-api")

    def snapshot(self) -> ResourceSnapshot:
        snap = super().snapshot()
        data = battery_status()  # 没有 termux-api 时返回 None，不抛异常
        if isinstance(data, dict):
            percentage = data.get("percentage")
            temperature = data.get("temperature")
            if isinstance(percentage, (int, float)):
                snap.battery = float(percentage)
            if isinstance(temperature, (int, float)):
                snap.temperature = float(temperature)
        return snap

    def capabilities(self) -> dict:
        """Termux 相关能力的可用性（给 /health 用）。"""
        return {"termux": _is_termux(), "termux_api": has_termux_api()}


def detect_host(profile: str = "auto", workdir: str = "."):
    """按配置选择 Host 实现：auto 时自动识别 Termux。"""
    if profile == "termux" or (profile == "auto" and _is_termux()):
        return TermuxHost(workdir=workdir)
    return GenericHost(workdir=workdir)


__all__ = ["TermuxHost", "detect_host"]
