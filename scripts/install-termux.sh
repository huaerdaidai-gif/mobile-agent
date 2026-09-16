#!/usr/bin/env bash
# Termux 安装脚本：检查 Python → 建目录 → 生成配置 → 安装 mobile-agent 命令 → 自检
#
# 用法（在手机 Termux 里）：
#   bash scripts/install-termux.sh              # 只检查，不自动装包
#   bash scripts/install-termux.sh --auto-deps  # 缺 python 时自动 pkg install python
#   bash scripts/install-termux.sh --with-termux-api   # 顺便装 termux-api（可选能力）
#
# 只用 Python 标准库；不装重量级依赖，不建虚拟环境。
set -eu

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
AUTO_DEPS=0
WITH_TERMUX_API=0
for arg in "$@"; do
  case "$arg" in
    --auto-deps) AUTO_DEPS=1 ;;
    --with-termux-api) WITH_TERMUX_API=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "未知参数：$arg"; exit 2 ;;
  esac
done

echo "== Mobile Agent Portable MVP 安装 =="
echo "项目目录：$PROJECT_DIR"

# 1. 环境检查
if [ -n "${PREFIX:-}" ] && echo "$PREFIX" | grep -q com.termux; then
  echo "[1/6] 环境：Termux（PREFIX=$PREFIX）"
else
  echo "[1/6] 注意：当前不像 Termux 环境（PREFIX=${PREFIX:-未设置}），仍会继续，但 termux-api 能力不可用"
fi

# 2. Python
if command -v python3 >/dev/null 2>&1; then
  echo "[2/6] Python：$(python3 -V 2>&1)"
else
  echo "[2/6] 未找到 python3"
  if [ "$AUTO_DEPS" = "1" ]; then
    echo "      执行 pkg update && pkg install -y python"
    pkg update -y && pkg install -y python
  else
    echo "      请先执行：pkg install python    （或加 --auto-deps 自动安装）"
    exit 1
  fi
fi

# 3. 可选依赖：termux-api（唤醒锁 / 通知 / 电池温度，全部可选）
if [ "$WITH_TERMUX_API" = "1" ]; then
  if command -v termux-battery-status >/dev/null 2>&1; then
    echo "[3/6] termux-api：已安装"
  else
    echo "[3/6] 安装 termux-api（可选，用于唤醒锁/通知/温度）"
    pkg install -y termux-api || echo "      termux-api 安装失败，不影响运行（会降级为不可用）"
  fi
else
  echo "[3/6] termux-api：跳过（加 --with-termux-api 可安装，纯可选）"
fi

# 4. 目录与配置
echo "[4/6] 准备目录与配置"
mkdir -p "$PROJECT_DIR/memory" "$PROJECT_DIR/runtime/state"
if [ -f "$PROJECT_DIR/config.yaml" ]; then
  echo "      已存在 config.yaml，保持不变"
elif [ -f "$PROJECT_DIR/config.example.yaml" ]; then
  cp "$PROJECT_DIR/config.example.yaml" "$PROJECT_DIR/config.yaml"
  echo "      已从 config.example.yaml 生成 config.yaml（记得改模型地址和 QQ 配置）"
else
  echo "      未找到 config.yaml / config.example.yaml，请手动创建"
fi

# 5. 安装 mobile-agent 命令
echo "[5/6] 安装命令"
CLI="$PROJECT_DIR/cli.py"
chmod +x "$CLI" 2>/dev/null || true
if [ -n "${PREFIX:-}" ] && [ -d "$PREFIX/bin" ]; then
  ln -sf "$CLI" "$PREFIX/bin/mobile-agent"
  echo "      已安装：$PREFIX/bin/mobile-agent"
else
  echo "      未找到 \$PREFIX/bin，可手动加别名：alias mobile-agent='python3 $CLI'"
fi

# 6. 自检（离线测试，不需要模型）
echo "[6/6] 自检（离线测试套件）"
TMP_BASE="${TMPDIR:-${PREFIX:-$PROJECT_DIR}}"
LOG_FILE="$TMP_BASE/ma-install-test.log"
mkdir -p "$TMP_BASE" 2>/dev/null || LOG_FILE="$PROJECT_DIR/ma-install-test.log"
if (cd "$PROJECT_DIR" && python3 -m unittest discover -s tests >"$LOG_FILE" 2>&1); then
  echo "      离线测试通过"
else
  echo "      离线测试有问题，详情见 $LOG_FILE"
  tail -5 "$LOG_FILE" 2>/dev/null || true
fi

cat <<'EOF'

== 安装完成，下一步 ==
1) 编辑配置：
     nano config.yaml
   至少确认：llm.base_url / llm.model 指向你的 llama-server
   （如果在手机本机跑：http://127.0.0.1:8080/v1）
2) 启动：
     mobile-agent start          # 或 bash scripts/start-termux.sh
3) 查看状态：
     mobile-agent status         # 期望看到 Host OK / Model OK / Gateway OK
4) 跑测试：
     mobile-agent test
5) 接 QQ：
     见 docs/QQ_SETUP.md
EOF
