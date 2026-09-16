#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mobile Agent v0.25.1 性能基准测试。

用法：
    python3 work/benchmark_v0251.py                     # 用 config.yaml 里的 base_url
    python3 work/benchmark_v0251.py --url http://127.0.0.1:8080/v1
    python3 work/benchmark_v0251.py --repeat 2 --project /path/to/mobile-agent

测量每个场景的：请求总耗时、首 token 时间、prompt tokens、completion tokens、
模型调用次数、工具调用次数、是否成功。默认每个场景 1 次（真实设备较慢）。

脚本只依赖项目自身代码与标准库，不会修改任何项目文件（记忆写入走项目的 memory.json）。
"""

import argparse
import os
import sys
import time

# 默认按脚本位置推断项目根目录（本文件在 <项目>/work/ 下）
_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PROJECT = os.path.dirname(_HERE)


def load_project(project_root):
    """把项目根目录加入 sys.path 并导入项目模块。"""
    sys.path.insert(0, project_root)
    import main as cli
    import llm as llm_module
    from agent import Agent
    from llm import LLM, LLMError
    return cli, llm_module, Agent, LLM, LLMError


def make_timed_llm(LLM, LLMError, **kwargs):
    """包装 LLM，记录每次请求的耗时、首 token 时间与 token 用量。"""

    class TimedLLM(LLM):
        def __init__(self, **kw):
            LLM.__init__(self, **kw)
            self.calls = []

        def _start(self):
            self.last_timings = {}
            self.last_usage = {}
            return time.time()

        def _finish(self, started, first_delta, ok, error=None):
            timings = self.last_timings or {}
            usage = self.last_usage or {}
            prompt_tokens = usage.get("prompt_tokens")
            if prompt_tokens is None and timings:
                prompt_tokens = int(timings.get("cache_n", 0)) + int(timings.get("prompt_n", 0))
            completion_tokens = usage.get("completion_tokens")
            if completion_tokens is None and timings:
                completion_tokens = timings.get("predicted_n")
            self.calls.append({
                "total": time.time() - started,
                "ttft": first_delta,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "prompt_ms": timings.get("prompt_ms"),
                "ok": ok,
                "error": error,
            })

        def chat(self, messages, **kwargs):
            started = self._start()
            try:
                text = LLM.chat(self, messages, **kwargs)
            except LLMError as exc:
                self._finish(started, None, False, str(exc))
                raise
            self._finish(started, time.time() - started, True)
            return text

        def chat_stream(self, messages, **kwargs):
            started = self._start()
            first_delta = [None]
            try:
                for delta in LLM.chat_stream(self, messages, **kwargs):
                    if first_delta[0] is None:
                        first_delta[0] = time.time() - started
                    yield delta
            except LLMError as exc:
                self._finish(started, first_delta[0], False, str(exc))
                raise
            self._finish(started, first_delta[0], True)

    return TimedLLM(**kwargs)


SCENARIOS = [
    ("A 普通聊天", "你好", "chat"),
    ("B 简单知识", "什么是人工智能？", "chat"),
    ("C time", "现在几点？", "tool"),
    ("D shell", "查看当前目录", "tool"),
    ("E file", "读取 README.md", "tool"),
]


def run_once(cli, Agent, LLM, LLMError, config, llm_config, agent_kwargs, question):
    """跑一个场景，返回统计信息。"""
    llm = make_timed_llm(LLM, LLMError, **llm_config)
    status = []
    agent = Agent(llm=llm, **agent_kwargs)
    agent._status = status.append

    visible = []
    started = time.time()
    error = None
    answer = ""
    try:
        answer = agent.ask(question, on_text=visible.append)
    except LLMError as exc:
        error = str(exc)
    total = time.time() - started

    calls = llm.calls
    prompt_tokens = sum(c["prompt_tokens"] or 0 for c in calls)
    completion_tokens = sum(c["completion_tokens"] or 0 for c in calls)
    ttft = next((c["ttft"] for c in calls if c["ttft"] is not None), None)
    prompt_ms = sum(c["prompt_ms"] or 0 for c in calls)
    return {
        "total": total,
        "ttft": ttft,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "prompt_ms": prompt_ms,
        "llm_calls": len(calls),
        "tool_calls": sum(1 for s in status if s.startswith("[调用工具")),
        "answer": answer,
        "error": error,
        "ok": bool(answer) and error is None,
    }


def main():
    parser = argparse.ArgumentParser(description="Mobile Agent v0.25.1 性能基准")
    parser.add_argument("--url", default=None, help="覆盖 config.yaml 里的 base_url")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="项目目录")
    parser.add_argument("--repeat", type=int, default=1, help="每个场景重复次数")
    args = parser.parse_args()

    cli, llm_module, Agent, LLM, LLMError = load_project(args.project)
    config = cli.load_config()
    if args.url:
        config["llm"]["base_url"] = args.url
    perf = config.get("performance") or {}

    server_info = llm_module.detect_server_context(config["llm"]["base_url"], timeout=5)
    effective = min(int(config["agent"]["max_context"]), server_info["context"]) \
        if server_info.get("context") else int(config["agent"]["max_context"])

    print("=" * 72)
    print("Mobile Agent v0.25.1 性能基准")
    print("接口: %s" % config["llm"]["base_url"])
    print("上下文: 配置 %s / 服务器 %s / 实际 %s（来源: %s）"
          % (config["agent"]["max_context"], server_info.get("context"), effective,
             server_info.get("source") or "配置值"))
    print("参数: max_output_tokens=%s auto_detect=%s repeat=%d"
          % (perf.get("max_output_tokens"), perf.get("auto_detect_context"), args.repeat))
    print("=" * 72)

    llm_config = dict(base_url=config["llm"]["base_url"], model=config["llm"]["model"],
                      temperature=float(config["llm"]["temperature"]),
                      timeout=int(config["llm"]["timeout"]),
                      max_tokens=int(perf.get("max_output_tokens", 512) or 0))
    memory_config = config.get("memory") or {}
    tools_config = config.get("tools") or {}
    agent_kwargs = dict(
        max_context=int(config["agent"]["max_context"]),
        max_steps=int(config["agent"]["max_steps"]),
        max_memory_entries=int(config["agent"]["max_memory_entries"]),
        stream=cli.stream_enabled(config),
        memory_enabled=bool(memory_config.get("enabled", True)),
        memory_max_items=int(perf.get("memory_max_items", 5)),
        tools_enabled=bool(tools_config.get("enabled", True)),
        legacy_protocol=bool(tools_config.get("legacy_protocol", False)),
        server_context=server_info.get("context"),
        tool_result_max_chars=int(perf.get("tool_result_max_chars", 4000)),
        memory_max_ratio=float(perf.get("memory_max_ratio", 0.10)),
        system_prompt_max_tokens=int(perf.get("system_prompt_max_tokens", 300)),
        verbose=False,
    )

    header = "%-12s %8s %8s %8s %8s %6s %6s %s" % (
        "场景", "总耗时", "首token", "prompt", "完成", "LLM", "工具", "结果")
    print(header)
    print("-" * 72)

    rows = []
    for label, question, kind in SCENARIOS:
        for index in range(max(1, args.repeat)):
            stat = run_once(cli, Agent, LLM, LLMError, config, llm_config, agent_kwargs, question)
            stat.update({"label": label, "kind": kind, "question": question, "run": index + 1})
            rows.append(stat)
            print("%-12s %7.2fs %7s %8d %8d %6d %6d %s" % (
                label if args.repeat == 1 else "%s#%d" % (label, index + 1),
                stat["total"],
                ("%.2fs" % stat["ttft"]) if stat["ttft"] is not None else "-",
                stat["prompt_tokens"], stat["completion_tokens"] or 0,
                stat["llm_calls"], stat["tool_calls"],
                "OK" if stat["ok"] else ("失败: %s" % stat["error"]),
            ))
            if stat["answer"]:
                print("            回答: %s" % stat["answer"].strip().replace("\n", " ")[:60])

    def average(items, key):
        values = [item[key] for item in items if item[key] is not None]
        return sum(values) / len(values) if values else 0

    print("-" * 72)
    for kind, name in (("chat", "普通聊天"), ("tool", "工具调用")):
        subset = [r for r in rows if r["kind"] == kind]
        if not subset:
            continue
        print("%s：%d 次 | 平均总耗时 %.2fs | 平均首 token %.2fs | 平均 prompt %d tokens | 平均 completion %d"
              % (name, len(subset), average(subset, "total"), average(subset, "ttft"),
                 average(subset, "prompt_tokens"), average(subset, "completion_tokens")))
    success = sum(1 for r in rows if r["ok"])
    print("成功率：%d/%d" % (success, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
