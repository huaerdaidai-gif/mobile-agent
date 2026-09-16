#!/usr/bin/env bash
# Termux 停止脚本：停止 Gateway → 释放唤醒锁
set -eu

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

python3 cli.py stop "$@"
if command -v termux-wake-unlock >/dev/null 2>&1; then
  termux-wake-unlock 2>/dev/null && echo "[termux] 已释放唤醒锁"
fi
