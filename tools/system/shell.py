# -*- coding: utf-8 -*-
"""shell 工具：执行安全的 shell 命令（Stage 2 迁移，安全策略与 v0.25.1 完全一致）。

安全策略（针对 1B~4B 小模型容易乱生成命令的情况）：
  1. 白名单：只允许执行 ALLOWED_COMMANDS 里列出的命令；
  2. 黑名单字符：出现管道、重定向、命令串联等字符时直接拒绝；
  3. 不使用 shell=True，参数用 shlex 拆分后直接执行，天然无法串联命令；
  4. 工作目录固定在项目根目录，并限制输出长度，保护上下文窗口。
"""

import os
import shlex
import subprocess

# 项目根目录（本文件位于 <项目>/tools/system/shell.py）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 允许执行的命令（只读类，安全优先）
ALLOWED_COMMANDS = {
    "ls", "pwd", "cat", "head", "tail", "wc", "echo", "date",
    "df", "du", "uname", "whoami", "id", "uptime", "free",
    "ps", "which", "file", "stat", "find", "grep", "sort", "uniq", "cut", "tree",
}

# 出现即拒绝的 shell 元字符（防止命令串联 / 注入 / 重定向写入）
DENIED_CHARS = ["|", ";", "&", ">", "<", "`", "$", "(", ")", "\n", "\r"]

# 默认输出上限（字符数），避免一次 ls 大目录就撑爆上下文
MAX_OUTPUT_CHARS = 4000


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """截断过长的输出，并给出明确提示。"""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...(输出过长，已截断)"


def run_command(command: str, timeout: int = 15, cwd: str = None,
                max_output: int = MAX_OUTPUT_CHARS) -> dict:
    """执行一条安全命令，返回 {"ok","stdout","stderr","returncode"}。"""
    command = (command or "").strip()
    if not command:
        return {"ok": False, "error": "命令为空"}

    for ch in DENIED_CHARS:
        if ch in command:
            return {"ok": False, "error": "命令包含不允许的字符 %r，已拒绝执行" % ch}

    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return {"ok": False, "error": "命令解析失败：%s" % exc}
    if not parts:
        return {"ok": False, "error": "命令为空"}

    program = os.path.basename(parts[0])
    if program not in ALLOWED_COMMANDS:
        return {"ok": False,
                "error": "命令 %s 不在白名单中。可用命令：%s"
                         % (program, ", ".join(sorted(ALLOWED_COMMANDS)))}

    workdir = cwd or PROJECT_ROOT
    try:
        proc = subprocess.run(parts, cwd=workdir, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout, shell=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "命令执行超时（%s 秒）" % timeout,
                "stdout": "", "stderr": "", "returncode": -1}
    except OSError as exc:
        return {"ok": False, "error": "命令执行失败：%s" % exc,
                "stdout": "", "stderr": "", "returncode": -1}

    stdout = _truncate(proc.stdout.decode("utf-8", "replace"), max_output)
    stderr = _truncate(proc.stderr.decode("utf-8", "replace"), max_output)
    return {"ok": proc.returncode == 0, "command": command, "stdout": stdout,
            "stderr": stderr, "returncode": proc.returncode}
