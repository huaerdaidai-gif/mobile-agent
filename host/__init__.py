# -*- coding: utf-8 -*-
"""Host 抽象：描述「跑在哪里」，但不包含任何具体平台实现。

具体实现（Termux / 通用 Linux / macOS）放在 adapter/platform/ 里。
Core 与 Runtime 只依赖这里的接口，不出现 Termux / Android / 机型名字。
"""

from host.base import Host, HostProfile, ResourceSnapshot

__all__ = ["Host", "HostProfile", "ResourceSnapshot"]
