#!/usr/bin/env bash
# Termux 状态脚本：打印进程 + 健康状态；不健康时可选发一条通知
set -eu

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

set +e
python3 cli.py status
STATUS=$?
set -e

if [ "$STATUS" != "0" ] && command -v termux-notification >/dev/null 2>&1; then
  termux-notification --title "Mobile Agent" --content "健康检查未通过，请查看 mobile-agent status" 2>/dev/null || true
fi

echo
echo "进程信息："
ps -o pid,etime,rss,args -p "$(cat runtime/state/gateway.pid 2>/dev/null || echo 0)" 2>/dev/null || echo "  （未运行）"
exit "$STATUS"
