#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手机端验收脚本（在 Termux 里运行，只用标准库）。

用法（在项目目录下）：
    python3 scripts/acceptance-phone.py            # Gateway 未启动时会自动启动
    python3 scripts/acceptance-phone.py --keep     # 验收结束后不关闭 Gateway

输出：每项 PASS/FAIL + TTFT / 总耗时 / 工具延迟 / Agent RSS，最后给出汇总。
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print("%-4s %-28s %s" % ("PASS" if ok else "FAIL", name, detail))


def request(url, payload=None, timeout=300):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers,
                                method="POST" if data else "GET")
    started = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
        elapsed = time.time() - started
    try:
        return json.loads(body), elapsed
    except ValueError:
        return body, elapsed


def stream_request(url, payload, timeout=300):
    """流式请求：返回 (完整文本, 首字节时间, 总时间)。"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    chunks, first = [], None
    started = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        while True:
            piece = response.read(64)
            if not piece:
                break
            if first is None:
                first = time.time() - started
            chunks.append(piece.decode("utf-8", "replace"))
    return "".join(chunks), first, time.time() - started


def rss_mb(pid):
    """从 /proc/<pid>/status 读 RSS（MB）。"""
    try:
        with open("/proc/%d/status" % pid, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        return None
    return None


def gateway_pid():
    path = os.path.join(PROJECT, "runtime", "state", "gateway.pid")
    try:
        with open(path, encoding="utf-8") as handle:
            pid = int(handle.read().strip())
        os.kill(pid, 0)
        return pid
    except (IOError, ValueError, OSError):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="结束后不停止 Gateway")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    sys.path.insert(0, PROJECT)
    from runtime import config as runtime_config
    config = runtime_config.load()
    port = args.port or int((config.get("gateway") or {}).get("port", 8787))
    base = "http://127.0.0.1:%d" % port

    started_by_us = False
    pid = gateway_pid()
    if not pid:
        print("Gateway 未运行，正在启动……")
        subprocess.run([sys.executable, "cli.py", "start"], cwd=PROJECT)
        started_by_us = True
        time.sleep(2)
        pid = gateway_pid()

    try:
        health, _ = request(base + "/health", timeout=60)
        components = (health or {}).get("components") or {}
        check("健康检查", bool(health.get("ok")),
              "{}".format({k: components.get(k, {}).get("ok") for k in
                           ("agent", "model", "gateway", "qq", "host")}))
        check("模型上下文探测", components.get("model", {}).get("context") is not None,
              "context=%s" % components.get("model", {}).get("context"))

        reply, total = request(base + "/api/chat", {"text": "你好", "session_id": "acc-1"})
        check("普通聊天", bool(reply.get("reply")), "总 %.2fs | %s" % (total, str(reply.get("reply"))[:40]))
        chat_total = total

        reply2, total2 = request(base + "/api/chat",
                                 {"text": "我刚才说了什么？", "session_id": "acc-1"})
        check("连续聊天（同一会话）", bool(reply2.get("reply")),
              "总 %.2fs | %s" % (total2, str(reply2.get("reply"))[:40]))

        reply3, total3 = request(base + "/api/chat",
                                 {"text": "记住我的节点叫 xiaomi12-node", "session_id": "acc-1"},
                                 timeout=60)
        check("Memory（记住 → 直写）", reply3.get("reply") == "已记住。",
              "总 %.2fs | %s" % (total3, str(reply3.get("reply"))[:20]))

        reply4, total4 = request(base + "/api/chat", {"text": "现在几点？", "session_id": "acc-1"})
        check("Tool（time）", "时间" in str(reply4.get("reply")) or "点" in str(reply4.get("reply")),
              "工具轮次总 %.2fs | %s" % (total4, str(reply4.get("reply"))[:40]))

        text, ttft, total5 = stream_request(base + "/api/chat/stream",
                                            {"text": "用一句话介绍你自己", "session_id": "acc-2"})
        check("Streaming", len(text) > 5,
              "TTFT %.2fs | 总 %.2fs | %s" % (ttft or -1, total5, text[:36].replace("\n", " ")))

        if pid:
            rss = rss_mb(pid)
            check("Agent RSS", rss is not None, "%.1f MB（pid=%d）" % (rss or -1, pid))
        else:
            check("Agent RSS", False, "拿不到 pid")

        print("\n汇总：普通聊天 %.2fs | 连续 %.2fs | 记忆 %.2fs | 工具 %.2fs | 流式 TTFT %.2fs/总 %.2fs"
              % (chat_total, total2, total3, total4, ttft or -1, total5))
    finally:
        if started_by_us and not args.keep:
            subprocess.run([sys.executable, "cli.py", "stop"], cwd=PROJECT)

    failed = [name for name, ok in RESULTS if not ok]
    print("\n共 %d 项，失败 %d 项" % (len(RESULTS), len(failed)))
    for name in failed:
        print("  FAILED:", name)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
