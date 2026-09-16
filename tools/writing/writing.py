# -*- coding: utf-8 -*-
"""writing 工具：保存文稿。

重要：写文章本身由 Primary 模型一次流式生成完成，**不再调用第二个 LLM**。
本工具只在用户明确说「保存成文件」时被调用，等价于受限的文件写入。
"""

import os

from tools.system.file import _relative, _resolve  # noqa: F401  路径安全复用文件工具
from tools.system.file import read_file, write_file

DEFAULT_SUFFIX = ".md"


def document_write(path: str, content: str = "") -> dict:
    """把文稿写入项目目录内的文件（无扩展名时默认 .md）。"""
    path = (path or "").strip()
    if not path:
        return {"ok": False, "error": "document_write: 需要文件路径"}
    if not os.path.splitext(path)[1]:
        path += DEFAULT_SUFFIX
    result = write_file(path, content if content is not None else "")
    if not result.get("ok"):
        return result
    return {"ok": True, "path": result["path"], "bytes": result["bytes"],
            "note": "文稿已保存（内容由主模型一次生成，未额外调用模型）"}


def document_read(path: str, max_chars: int = 4000) -> dict:
    """读回文稿（复用文件工具的路径限制）。"""
    return read_file(path, max_chars=max_chars)


__all__ = ["document_write", "document_read", "DEFAULT_SUFFIX", "_resolve", "_relative"]
