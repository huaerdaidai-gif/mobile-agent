# -*- coding: utf-8 -*-
"""time 工具：返回当前时间（Stage 2 从 tools/time.py 迁移到这里，原路径保留兼容包装）。"""

import time as _time
from datetime import datetime

# 星期几的中文名，避免依赖 locale（Termux 上 locale 常常没装全）
_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def get_current_time() -> dict:
    """返回当前本地时间：可读时间、星期、时区、时间戳。"""
    now = datetime.now()
    return {
        "ok": True,
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "weekday": _WEEKDAYS[now.weekday()],
        "timezone": _time.tzname[0] if _time.tzname else "",
        "timestamp": int(now.timestamp()),
    }
