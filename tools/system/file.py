# -*- coding: utf-8 -*-
"""file 工具：读写文件（Stage 2 迁移，逻辑与 v0.25.1 完全一致）。

安全策略：所有路径都会解析成绝对路径，并且必须位于项目目录内，
因此模型无法通过 ../../etc/passwd 这类路径读到项目外的文件。
"""

import os
import fnmatch
import re

# 项目根目录（本文件位于 <项目>/tools/system/file.py）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 单次读取的字符上限，防止大文件占满上下文窗口
MAX_READ_CHARS = 4000
PRIVATE_KEY_HEADER = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE)


def _resolve(path: str, project_root: str = None) -> str:
    """把相对路径解析为项目目录内的绝对路径，越界时抛出 ValueError。"""
    if not path:
        raise ValueError("路径为空")
    root_value = os.path.abspath(project_root) if project_root else PROJECT_ROOT
    if project_root and (os.path.islink(root_value) or not os.path.isdir(root_value)):
        raise ValueError("项目目录无效")
    full = path if os.path.isabs(path) else os.path.join(root_value, path)
    full = os.path.realpath(full)
    root = os.path.realpath(root_value)
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("路径越界：只允许访问项目目录内的文件")
    return full


def _relative(path: str, project_root: str = None) -> str:
    """返回相对项目根目录的路径，便于日志和结果展示。"""
    root = os.path.realpath(project_root) if project_root else PROJECT_ROOT
    return os.path.relpath(path, root).replace(os.sep, "/")


def _sensitive_path(relative: str) -> bool:
    """Never send credentials, private keys, or Git internals to the model."""
    parts = relative.replace("\\", "/").split("/")
    for part in parts:
        name = part.lower()
        if name == ".git" or name == ".env" or name.startswith((".env.", ".env_", ".env-")):
            return True
        if name.startswith(".git-") and "credential" in name:
            return True
        if name in {".aws", ".ssh", ".docker", ".kube", ".npmrc", ".pypirc", ".netrc",
                    ".git-credentials", ".gitconfig", "key.properties"}:
            return True
        if name.endswith((".env", ".envrc", ".pem", ".key", ".p8", ".p12", ".pfx",
                          ".jks", ".keystore", ".ppk", ".tfstate")):
            return True
        if name.startswith(("credential", "credentials", "service-account")):
            return True
        if name.endswith(("_rsa", "_dsa", "_ecdsa", "_ed25519")):
            return True
        if re.match(r"^id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?$", name):
            return True
        if fnmatch.fnmatch(name, "*.pem") or fnmatch.fnmatch(name, "*.key"):
            return True
        if "secret" in name or ("token" in name and "tokeniz" not in name):
            return True
    return False


def read_file(path: str, max_chars: int = MAX_READ_CHARS, project_root: str = None) -> dict:
    """读取文本文件，返回 {"ok": True, "path": ..., "content": ...}。"""
    try:
        full = _resolve(path, project_root=project_root)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    relative = _relative(full, project_root)
    if _sensitive_path(relative):
        return {"ok": False, "error": "出于安全考虑，不能读取密钥或 Git 内部文件"}
    if not os.path.exists(full):
        return {"ok": False, "error": "文件不存在：%s" % relative}
    if os.path.isdir(full):
        return {"ok": False, "error": "%s 是目录" % relative}

    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as exc:
        return {"ok": False, "error": "读取失败：%s" % exc}

    if PRIVATE_KEY_HEADER.search(content):
        return {"ok": False, "error": "出于安全考虑，不能读取密钥或 Git 内部文件"}

    return {
        "ok": True,
        "path": relative,
        "content": content[:max_chars],
        "truncated": len(content) > max_chars,
    }


def write_file(path: str, content: str, project_root: str = None) -> dict:
    """写入文本文件（覆盖写），父目录不存在时自动创建。"""
    try:
        full = _resolve(path, project_root=project_root)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if os.path.isdir(full):
        return {"ok": False, "error": "%s 是目录，不能写入" % _relative(full, project_root)}

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
        "path": _relative(full, project_root),
        "bytes": len((content or "").encode("utf-8")),
    }
