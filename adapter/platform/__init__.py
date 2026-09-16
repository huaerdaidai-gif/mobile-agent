# -*- coding: utf-8 -*-
"""平台适配：把抽象 Host 落到具体系统上。"""

from adapter.platform.generic_host import GenericHost
from adapter.platform.termux_host import TermuxHost, detect_host

__all__ = ["GenericHost", "TermuxHost", "detect_host"]
