# -*- coding: utf-8 -*-
"""统一日志：按组件打标签，并且不泄漏 token。

组件标签固定为：CORE / MODEL / TOOL / GATEWAY / QQ / HOST / RUNTIME。
输出到 stdout（Termux 里方便直接看）和可选文件。
"""

import logging
import os
import re
import sys

COMPONENTS = ("CORE", "MODEL", "TOOL", "GATEWAY", "QQ", "HOST", "RUNTIME")

_LOGGER_NAME = "mobile-agent"
_configured = False

# 形如 token=abc123 / "secret": "abc123" 的内容会被打码
_SECRET_PATTERN = re.compile(
    r"(?i)\b(token|secret|password|passwd|api_key|apikey|authorization)\b"
    r"(\s*[:=]\s*|\s+)([^\s,;\"'}\]]+)"
)


def redact(text: str) -> str:
    """把日志里可能出现的密钥替换成 ***。"""
    if not text:
        return ""
    return _SECRET_PATTERN.sub(lambda m: "%s%s***" % (m.group(1), m.group(2)), str(text))


class _ComponentFilter(logging.Filter):
    """给每条日志补上 component 字段（没有就默认 RUNTIME）。"""

    def filter(self, record):
        if not hasattr(record, "component") or not record.component:
            record.component = "RUNTIME"
        return True


def setup(level: str = "INFO", log_file: str = None) -> None:
    """初始化日志（可重复调用，只生效一次）。"""
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-5s [%(component)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    stream.addFilter(_ComponentFilter())
    logger.addHandler(stream)

    if log_file:
        try:
            directory = os.path.dirname(log_file)
            if directory and not os.path.isdir(directory):
                os.makedirs(directory, exist_ok=True)
            handler = logging.FileHandler(log_file, encoding="utf-8")
            handler.setFormatter(formatter)
            handler.addFilter(_ComponentFilter())
            logger.addHandler(handler)
        except OSError as exc:  # 日志文件写不了不影响运行
            logger.warning("日志文件不可用：%s", redact(str(exc)), extra={"component": "RUNTIME"})
    logger.propagate = False
    _configured = True


def log(component: str, message: str, level: str = "INFO", **extra) -> None:
    """写一条带组件标签的日志。"""
    logger = logging.getLogger(_LOGGER_NAME)
    if not _configured:
        setup()
    text = redact(message)
    for key, value in (extra or {}).items():
        text += " %s=%s" % (key, redact(str(value)))
    logger.log(getattr(logging, str(level).upper(), logging.INFO), text,
               extra={"component": component if component in COMPONENTS else "RUNTIME"})
