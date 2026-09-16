# -*- coding: utf-8 -*-
"""Core 回归测试：确认 v0.25.1 的行为在新增架构后没有变化（全部离线）。"""

import json
import os
import tempfile
import unittest

from tests import helpers


class StubLLM(object):
    """按脚本逐条返回的假模型。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0
        self.last_timings = {}
        self.last_usage = {}

    def chat(self, messages, **kwargs):
        self.calls += 1
        return self.replies.pop(0) if self.replies else "（没有更多预设回答）"

    def chat_stream(self, messages, **kwargs):
        yield self.chat(messages)


def make_agent(replies, tmpdir, **overrides):
    from agent import Agent
    llm = StubLLM(replies)
    params = dict(verbose=False, stream=True, max_context=2048, server_context=2048,
                  memory_path=os.path.join(tmpdir, "memory.json"),
                  memory_enabled=True, memory_max_items=5, tool_result_max_chars=4000,
                  memory_max_ratio=0.10, system_prompt_max_tokens=300)
    params.update(overrides)
    agent = Agent(llm=llm, **params)
    return agent, llm


class CoreRegressionTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ma-test-")

    def test_tool_loop_executes_and_feeds_back(self):
        agent, llm = make_agent(
            ['{"type":"tool_call","name":"time","arguments":{}}', "现在的时间已经拿到。"],
            self.tmpdir)
        status = []
        agent._status = status.append
        answer = agent.ask("现在几点？")
        self.assertEqual(answer, "现在的时间已经拿到。")
        self.assertTrue(any("[调用工具: time]" in item for item in status))
        self.assertEqual(llm.calls, 2)
        # 工具结果必须以统一格式回灌，且保持 JSON 外壳完整
        tool_messages = [m["content"] for m in agent.history if m["content"].startswith("工具结果：")]
        self.assertTrue(tool_messages)
        payload = json.loads(tool_messages[0][len("工具结果："):])
        self.assertTrue(payload["ok"])

    def test_memory_intent_skips_model(self):
        agent, llm = make_agent(["不应该被调用"], self.tmpdir)
        status = []
        agent._status = status.append
        answer = agent.ask("记住我喜欢喝咖啡")
        self.assertEqual(answer, "已记住。")
        self.assertEqual(llm.calls, 0)
        with open(agent.memory_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["history"][0]["user"], "我喜欢喝咖啡")

    def test_chat_tool_gate_blocks_spurious_tool(self):
        agent, llm = make_agent(
            ['{"type":"tool_call","name":"shell","arguments":{"command":"ls"}}', "你好呀。"],
            self.tmpdir)
        status = []
        agent._status = status.append
        answer = agent.ask("你好")
        self.assertEqual(answer, "你好呀。")
        self.assertFalse(any("[调用工具" in item for item in status))
        self.assertTrue(any("聊天门控" in item for item in status))

    def test_tool_result_truncation_keeps_envelope(self):
        agent, _llm = make_agent([], self.tmpdir)
        big = "内容" * 5000
        limited = agent._limit_tool_result(big)
        self.assertTrue(limited.startswith("[工具结果过长，已截断]"))
        message = {"role": "user", "content": "工具结果：" + json.dumps(
            {"ok": True, "result": limited}, ensure_ascii=False)}
        shrunk = agent._shrink_tool_result(message["content"], 50)
        payload = json.loads(shrunk["content"][len("工具结果："):])
        self.assertTrue(payload["ok"])
        self.assertIn("已截断", payload["result"])

    def test_prompt_budget_and_history_trim(self):
        agent, _llm = make_agent([], self.tmpdir)
        for index in range(40):
            agent.history.append({"role": "user", "content": "很长的历史消息" * 20})
            agent.history.append({"role": "assistant", "content": "很长的历史回答" * 20})
        agent._trim_history()
        self.assertLess(len(agent.history), 80)
        messages = agent._build_messages()
        from agent import estimate_tokens
        used = sum(estimate_tokens(m["content"]) for m in messages)
        self.assertLessEqual(used, int(2048 * 0.60) + 8)

    def test_memory_reinjection_respects_max_items(self):
        memory_path = os.path.join(self.tmpdir, "memory.json")
        entries = [{"time": "t", "user": "问题%d" % i, "assistant": "回答%d" % i}
                   for i in range(1, 11)]
        with open(memory_path, "w", encoding="utf-8") as handle:
            json.dump({"version": "0.25.1", "history": entries}, handle, ensure_ascii=False)
        agent, _llm = make_agent([], self.tmpdir, memory_max_items=3)
        agent.memory_path = memory_path
        agent.memory = agent._load_memory()
        agent.memory_block, agent.memory_items = agent._build_memory_block()
        self.assertEqual(agent.memory_items, 3)
        system = agent._build_messages()[0]["content"]
        self.assertIn("【历史记忆】", system)
        self.assertIn("问题10", system)

    def test_flat_arguments_and_punctuation_are_accepted(self):
        from tool_parser import parse_tool_call
        flat = '{"type":"tool_call","name":"file","action":"read","path":"README.md"}'
        parsed = parse_tool_call(flat, allow_flat=True)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["arguments"], {"action": "read", "path": "README.md"})
        full_width = '{"type":"tool_call","name":"time","arguments\uff1a{}}'
        self.assertIsNone(parse_tool_call(full_width))
        # 全角标点、齐全引号的情况可以被归一化救回来
        fixed = '{"type":"tool_call","name":"time"\uff0c"arguments":{}}'
        self.assertIsNotNone(parse_tool_call(fixed, normalize=True))


if __name__ == "__main__":
    unittest.main()
