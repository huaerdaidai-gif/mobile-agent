#!/usr/bin/env bash
# Termux 启动脚本：可选申请唤醒锁 → 启动 Gateway → 打印状态
set -eu

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock 2>/dev/null && echo "[termux] 已申请唤醒锁（避免后台被系统挂起）"
else
  echo "[termux] 未安装 termux-api，跳过唤醒锁（可选）"
fi

python3 cli.py start "$@"
echo
python3 cli.py status || true
