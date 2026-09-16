#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mobile-agent 命令行入口（标准库实现）。

用法：
    mobile-agent start            启动 Gateway（后台）
    mobile-agent stop             停止 Gateway
    mobile-agent status           查看进程 + 健康状态
    mobile-agent test             跑测试套件（--offline 只跑离线测试）
    mobile-agent health           打印 /health 结果
    mobile-agent chat             不进 Gateway，直接终端聊天
    mobile-agent config           打印生效的配置摘要（敏感值已打码）

安装脚本会把本文件软链到 $PREFIX/bin/mobile-agent。
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

# 真实路径（软链安装时也能找到项目目录）
PROJECT_ROOT = os.path.dirname(os.path.realpath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

STATE_DIR = os.path.join(PROJECT_ROOT, "runtime", "state")
PID_FILE = os.path.join(STATE_DIR, "gateway.pid")
LOG_FILE = os.path.join(STATE_DIR, "gateway.log")


def _ensure_state_dir() -> None:
    if not os.path.isdir(STATE_DIR):
        os.makedirs(STATE_DIR, exist_ok=True)


def read_pid():
    """读取 pid 文件，返回存活的 pid 或 None。"""
    try:
        with open(PID_FILE, "r", encoding="utf-8") as handle:
            pid = int(handle.read().strip())
    except (IOError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def http_get(url: str, timeout: int = 10):
    """GET 一个 JSON 接口，返回 (ok, payload)。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return True, json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code < 500, json.loads(body)
        except ValueError:
            return False, {"ok": False, "error": "HTTP %s" % exc.code}
    except Exception as exc:
        return False, {"ok": False, "error": str(exc)}


def load_config(path=None):
    from runtime import config as runtime_config
    return runtime_config.load(path)


def gateway_url(config, path="/health"):
    host = str((config.get("gateway") or {}).get("host", "127.0.0.1"))
    port = int((config.get("gateway") or {}).get("port", 8787))
    return "http://%s:%d%s" % (host, port, path)


# ---------------- 子命令 ----------------

def cmd_start(args) -> int:
    _ensure_state_dir()
    config = load_config(args.config)
    running = read_pid()
    if running:
        print("Gateway 已在运行（pid=%d）" % running)
        return 0
    host = (config.get("gateway") or {}).get("host", "127.0.0.1")
    port = (config.get("gateway") or {}).get("port", 8787)
    print("启动 Gateway（http://%s:%s），日志：%s" % (host, port, LOG_FILE))
    with open(LOG_FILE, "ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "gateway.server"] + (["--config", args.config] if args.config else []),
            cwd=PROJECT_ROOT, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    with open(PID_FILE, "w", encoding="utf-8") as handle:
        handle.write(str(process.pid))
    for _ in range(20):  # 最多等 10 秒，确认端口起来了
        time.sleep(0.5)
        if process.poll() is not None:
            print("启动失败，请看日志：%s" % LOG_FILE)
            return 1
        ok, payload = http_get(gateway_url(config, "/health?fast=1"), timeout=3)
        if ok and payload:
            print("已启动（pid=%d）" % process.pid)
            print("提示：mobile-agent status / health 可查看状态")
            return 0
    print("进程已启动（pid=%d），但健康检查还没就绪，可稍后执行 mobile-agent status" % process.pid)
    return 0


def cmd_stop(args) -> int:
    pid = read_pid()
    if not pid:
        print("Gateway 未在运行")
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        return 0
    try:
        os.kill(pid, 15)
    except OSError as exc:
        print("发送停止信号失败：%s" % exc)
    for _ in range(20):
        time.sleep(0.25)
        try:
            os.kill(pid, 0)
        except OSError:
            break
    else:
        try:
            os.kill(pid, 9)
            print("进程未响应，已强制结束")
        except OSError:
            pass
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
    print("已停止（pid=%d）" % pid)
    return 0


def cmd_status(args) -> int:
    config = load_config(args.config)
    pid = read_pid()
    print("进程：%s" % ("运行中 pid=%d" % pid if pid else "未运行"))
    ok, payload = http_get(gateway_url(config, "/health"), timeout=int(args.timeout))
    if not ok and not payload.get("components"):
        print("Gateway：无法连接（%s）" % payload.get("error"))
        return 1
    from gateway.health import render_text
    print(render_text(payload))
    return 0 if payload.get("ok") else 1


def cmd_health(args) -> int:
    config = load_config(args.config)
    ok, payload = http_get(gateway_url(config, "/health"), timeout=int(args.timeout))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok and payload.get("ok") else 1


def cmd_test(args) -> int:
    """跑测试套件；默认还会做一次真实模型连通性检查。"""
    print("== 单元测试（tests/）==")
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
    result = subprocess.run(command, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        print("单元测试失败")
        return result.returncode
    if args.offline:
        print("跳过模型连通性检查（--offline）")
        return 0
    print("== 模型连通性检查 ==")
    config = load_config(args.config)
    from adapter.model import build_provider
    try:
        provider = build_provider(config)
        health = provider.health()
    except Exception as exc:
        print("模型检查异常：%s" % exc)
        return 2
    print(json.dumps(health, ensure_ascii=False, indent=2))
    return 0 if health.get("ok") else 2


def cmd_config(args) -> int:
    from runtime import logging as agent_log
    config = load_config(args.config)
    from runtime import config as runtime_config
    summary = {
        "model": runtime_config.model_settings(config),
        "gateway": config.get("gateway"),
        "qq": config.get("qq"),
        "host": config.get("host"),
        "context_limit": runtime_config.context_limit(config),
        "tool_limit": runtime_config.tool_limit(config),
        "extension": config.get("extension"),
    }
    print(agent_log.redact(json.dumps(summary, ensure_ascii=False, indent=2)))
    for issue in runtime_config.warnings(config):
        print("[告警] %s" % issue)
    return 0


def cmd_chat(args) -> int:
    """不进 Gateway，直接在终端聊天（等价于 v0.25.1 的 main.py）。"""
    from runtime.service import AgentService
    config = load_config(args.config)
    service = AgentService(config=config)
    print("Mobile Agent 终端会话（输入 exit 退出）")
    while True:
        try:
            text = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in ("exit", "quit", "退出"):
            break
        try:
            service.ask(text, session_id="cli", on_text=lambda chunk: print(chunk, end="", flush=True))
        except Exception as exc:
            print("[错误] %s" % exc)
        print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mobile-agent", description="Portable Agent MVP")
    parser.add_argument("--config", default=None, help="config.yaml 路径（默认用项目里的）")
    parser.add_argument("--timeout", default=30, help="HTTP 超时（秒）")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("start", help="启动 Gateway")
    sub.add_parser("stop", help="停止 Gateway")
    sub.add_parser("status", help="查看进程与健康状态")
    sub.add_parser("health", help="打印 /health JSON")
    test_parser = sub.add_parser("test", help="跑测试套件")
    test_parser.add_argument("--offline", action="store_true", help="只跑离线测试")
    sub.add_parser("config", help="打印配置摘要")
    sub.add_parser("chat", help="终端聊天")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "start": cmd_start, "stop": cmd_stop, "status": cmd_status, "health": cmd_health,
        "test": cmd_test, "config": cmd_config, "chat": cmd_chat,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
