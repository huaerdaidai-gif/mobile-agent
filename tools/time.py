# -*- coding: utf-8 -*-
"""兼容包装：实现已迁移到 tools/core/time.py（v0.25.1 的 import 路径保持可用）。"""

from tools.core.time import get_current_time  # noqa: F401

__all__ = ["get_current_time"]
