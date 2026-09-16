# -*- coding: utf-8 -*-
"""兼容包装：实现已迁移到 tools/system/file.py（安全策略与参数完全一致）。"""

from tools.system.file import (  # noqa: F401
    MAX_READ_CHARS,
    PROJECT_ROOT,
    _relative,
    _resolve,
    read_file,
    write_file,
)

__all__ = ["read_file", "write_file", "MAX_READ_CHARS", "PROJECT_ROOT"]
