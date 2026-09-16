# -*- coding: utf-8 -*-
"""兼容包装：实现已迁移到 tools/system/shell.py（白名单与安全策略完全一致）。"""

from tools.system.shell import (  # noqa: F401
    ALLOWED_COMMANDS,
    DENIED_CHARS,
    MAX_OUTPUT_CHARS,
    PROJECT_ROOT,
    _truncate,
    run_command,
)

__all__ = ["run_command", "ALLOWED_COMMANDS", "DENIED_CHARS", "MAX_OUTPUT_CHARS"]
