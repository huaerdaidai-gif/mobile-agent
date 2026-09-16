# -*- coding: utf-8 -*-
"""file 工具：读写文件（Stage 2 迁移，逻辑与 v0.25.1 完全一致）。

安全策略：所有路径都会解析成绝对路径，并且必须位于项目目录内，
因此模型无法通过 ../../etc/passwd 这类路径读到项目外的文件。
"""

import os

# 项目根目录（本文件位于 <项目>/tools/system/file.py）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 单次读取的字符上限，防止大文件占满上下文窗口
MAX_READ_CHARS = 4000


def _resolve(path: str) -> str:
    """把相对路径解析为项目目录内的绝对路径，越界时抛出 ValueError。"""
    if not path:
        raise ValueError("路径为空")
    full = path if os.path.isabs(path) else os.path.join(PROJECT_ROOT, path)
    full = os.path.realpath(full)
    root = os.path.realpath(PROJECT_ROOT)
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("路径越界：只允许访问项目目录 %s 内的文件" % PROJECT_ROOT)
    return full


def _relative(path: str) -> str:
    """返回相对项目根目录的路径，便于日志和结果展示。"""
    return os.path.relpath(path, PROJECT_ROOT).replace(os.sep, "/")


def read_file(path: str, max_chars: int = MAX_READ_CHARS) -> dict:
    """读取文本文件，返回 {"ok": True, "path": ..., "content": ...}。"""
    try:
        full = _resolve(path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if not os.path.exists(full):
        return {"ok": False, "error": "文件不存在：%s" % _relative(full)}
    if os.path.isdir(full):
        return {"ok": False, "error": "%s 是目录，请用 shell 工具执行 ls 查看" % _relative(full)}

    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as exc:
        return {"ok": False, "error": "读取失败：%s" % exc}

    return {
        "ok": True,
        "path": _relative(full),
        "content": content[:max_chars],
        "truncated": len(content) > max_chars,
    }


def write_file(path: str, content: str) -> dict:
    """写入文本文件（覆盖写），父目录不存在时自动创建。"""
    try:
        full = _resolve(path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if os.path.isdir(full):
        return {"ok": False, "error": "%s 是目录，不能写入" % _relative(full)}

    try:
        parent = os.path.dirname(full)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content if content is not None else "")
    except OSError as exc:
        return {"ok": False, "error": "写入失败：%s" % exc}

    return {
        "ok": True,
        "path": _relative(full),
        "bytes": len((content or "").encode("utf-8")),
    }
