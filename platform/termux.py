# -*- coding: utf-8 -*-
"""Termux（Android）兼容层。

设计原则：
  1. 只做「检测 + 调用 termux-api 命令」，不引入任何 Python 依赖；
  2. Termux API 不存在时绝不抛异常，统一返回结构化的不可用结果；
  3. 所有外部命令都有超时，避免在手机上卡死 CLI。

返回值约定：
  可用时   -> {"ok": True, ...}
  不可用时 -> {"ok": False, "error": "人类可读的原因"}
"""

import json
import os
import shutil
import subprocess

# 调用外部命令的超时（秒）
_COMMAND_TIMEOUT = 10

# Termux 环境的特征路径（存在即认为是 Termux/Android 环境）
_TERMUX_MARKERS = (
    "/data/data/com.termux/files/usr",
    "/data/data/com.termux/files/home",
)


def _uname_release() -> str:
    """返回内核版本字符串，失败时返回空串。"""
    try:
        return os.uname().release.lower()
    except (AttributeError, OSError):
        return ""


def _run(args, timeout: int = _COMMAND_TIMEOUT):
    """执行外部命令，返回 (returncode, stdout, stderr, error)；从不抛异常。"""
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return None, "", "", "命令超时（%s 秒）" % timeout
    except OSError as exc:
        return None, "", "", "命令无法执行：%s" % exc
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", "replace").strip(),
        proc.stderr.decode("utf-8", "replace").strip(),
        None,
    )


def is_android() -> bool:
    """粗略判断是否运行在 Android 上（不依赖 termux-api）。"""
    if os.uname().sysname.lower() == "linux" and "android" in _uname_release():
        return True
    return any(os.path.exists(path) for path in _TERMUX_MARKERS)


def is_termux() -> bool:
    """判断是否运行在 Termux 中。

    依据：TERMUX_VERSION 环境变量、PREFIX 路径、以及 Termux 的固定目录。
    """
    if os.environ.get("TERMUX_VERSION"):
        return True
    if "com.termux" in os.environ.get("PREFIX", ""):
        return True
    return any(os.path.exists(path) for path in _TERMUX_MARKERS)


def has_termux_api() -> bool:
    """检测 termux-api 是否可用（只看命令是否存在，不需要安装 Python 包）。"""
    return shutil.which("termux-notification") is not None or shutil.which("termux-wake-lock") is not None


def wake_lock(tag: str = "mobile-agent") -> dict:
    """申请 Termux 唤醒锁，避免长任务被系统挂起。Termux API 缺失时返回不可用状态。"""
    if not shutil.which("termux-wake-lock"):
        return {"ok": False, "error": "Termux API 不可用（缺少 termux-wake-lock 命令）"}
    code, _out, err, error = _run(["termux-wake-lock"])
    if error:
        return {"ok": False, "error": error}
    if code != 0:
        return {"ok": False, "error": err or "termux-wake-lock 返回 %s" % code}
    return {"ok": True, "tag": tag}


def notification(title: str = "Mobile Agent", content: str = "") -> dict:
    """发送一条 Termux 通知。Termux API 缺失时返回不可用状态。"""
    if not shutil.which("termux-notification"):
        return {"ok": False, "error": "Termux API 不可用（缺少 termux-notification 命令）"}
    code, _out, err, error = _run(
        ["termux-notification", "--title", str(title), "--content", str(content)]
    )
    if error:
        return {"ok": False, "error": error}
    if code != 0:
        return {"ok": False, "error": err or "termux-notification 返回 %s" % code}
    return {"ok": True, "title": title}


def battery_status():
    """读取电池状态。

    可用时返回 termux-battery-status 的解析结果（附带 ok=True）；
    不可用（没有 Termux API、命令失败、返回不是 JSON）时返回 None。
    """
    if not shutil.which("termux-battery-status"):
        return None
    code, out, _err, error = _run(["termux-battery-status"])
    if error or code != 0 or not out:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    data["ok"] = True
    return data
