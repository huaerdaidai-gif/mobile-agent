# -*- coding: utf-8 -*-
"""Stage 2 测试：工具组门控、Schema 就近放置、prompt cache 稳定性、分级计时。"""

import json
import os
import tempfile
import unittest

from agent import Agent, select_tool_groups
from tests import helpers
from tools import registry


class StubLLM(object):
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.replies.pop(0) if self.replies else "好的。"

    def chat_stream(self, messages, **kwargs):
        self.calls.append(messages)
        yield self.replies.pop(0) if self.replies else "好的。"


def make_agent(replies, tmpdir):
    return Agent(llm=StubLLM(replies), verbose=False, stream=True, max_context=2048,
                 server_context=2048, memory_path=os.path.join(tmpdir, "memory.json"),
                 memory_enabled=True, memory_max_items=3)


class GateGroupsTest(unittest.TestCase):

    def setUp(self):
        registry.configure({})
        self.tmpdir = tempfile.mkdtemp(prefix="ma-gate-")

    def tearDown(self):
        registry.configure({})

    # ---- 关键词与分组（保守优先）----

    def test_plain_chat_selects_no_group(self):
        for text in ("你好", "介绍一下你自己", "看看这个", "查查资料吧", "你知道吗",
                     "今天心情怎么样", "谢谢你", "讲个笑话"):
            self.assertEqual(select_tool_groups(text), [], text)

    def test_group_selection(self):
        self.assertEqual(select_tool_groups("现在几点？"), ["core"])
        self.assertEqual(select_tool_groups("查看当前目录"), ["system"])
        self.assertEqual(select_tool_groups("读取 README.md"), ["system"])
        self.assertEqual(select_tool_groups("北京天气怎么样"), ["web"])
        self.assertEqual(select_tool_groups("搜索一下 llama.cpp"), ["web"])
        # 「保存成文件」命中 writing；同时「文件」也会命中 system 组（合理）
        self.assertIn("writing", select_tool_groups("把这段话保存成文件"))

    def test_disabled_groups_are_not_selected(self):
        registry.configure({"tools": {"web": {"enabled": False}}})
        self.assertEqual(select_tool_groups("北京天气怎么样"), [])

    def test_vision_words_do_not_select_when_group_disabled(self):
        # 视觉组默认关闭 → 选不出组（模型也就不会拿到视觉 schema）
        self.assertEqual(select_tool_groups("看看这张图"), [])
        registry.configure({"tools": {"vision": {"enabled": True}}})
        self.assertEqual(select_tool_groups("看看这张图"), ["vision"])

    # ---- Schema 放置与 prompt cache ----

    def test_system_prompt_has_no_tool_list(self):
        agent = make_agent([], self.tmpdir)
        # system prompt 里不能出现任何具体工具名（工具清单只按需贴在 user 消息里）
        for name in registry.all_tool_names():
            self.assertNotIn("- %s：" % name, agent.system_prompt, name)
        # 协议还在（固定前缀的一部分）
        self.assertIn("tool_call", agent.system_prompt)
        # 固定前缀体量要小（省 prompt processing）
        from agent import estimate_tokens
        self.assertLessEqual(estimate_tokens(agent.system_prompt), 140)

    def test_system_prompt_is_stable_across_groups(self):
        """不同工具组不能让 system prefix 变化（否则 prompt cache 失效）。"""
        agent = make_agent([], self.tmpdir)
        first = agent._build_messages_v2(["core"])[0]["content"]
        second = agent._build_messages_v2(["web"])[0]["content"]
        third = agent._build_messages_v2([])[0]["content"]
        self.assertEqual(first, second)
        self.assertEqual(second, third)

    def test_schema_goes_into_current_user_message(self):
        agent = make_agent([], self.tmpdir)
        agent.history.append({"role": "user", "content": "北京天气怎么样"})
        messages = agent._build_messages_v2(["web"])
        self.assertEqual(messages[0]["role"], "system")
        self.assertNotIn("weather", messages[0]["content"])
        self.assertIn("weather", messages[-1]["content"])
        self.assertIn("北京天气怎么样", messages[-1]["content"])

    def test_plain_chat_has_no_schema_tokens(self):
        agent = make_agent([], self.tmpdir)
        agent.history.append({"role": "user", "content": "你好"})
        messages = agent._build_messages_v2([])
        self.assertEqual(messages[-1]["content"], "你好")

    def test_history_is_not_polluted_by_schema(self):
        agent = make_agent(["你好呀"], self.tmpdir)
        agent.ask("你好")
        for message in agent.history:
            self.assertNotIn("可用工具", message.get("content", ""))

    # ---- 端到端（stub 模型）----

    def test_tool_loop_with_group_schema(self):
        agent = make_agent(['{"type":"tool_call","name":"time","arguments":{}}', "现在是 X。"],
                           self.tmpdir)
        status = []
        agent._status = status.append
        answer = agent.ask("现在几点？")
        self.assertEqual(answer, "现在是 X。")
        self.assertTrue(any("[调用工具: time]" in item for item in status))
        # 第二次调用时，工具结果也要在同一轮消息里回灌
        second_call = agent.llm.calls[-1]
        self.assertTrue(any(str(m.get("content", "")).startswith("工具结果：") for m in second_call))

    def test_timing_fields_recorded(self):
        agent = make_agent(["你好呀"], self.tmpdir)
        agent.ask("你好")
        timing = agent.last_timing
        for key in ("total_ms", "gate_ms", "route_ms", "schema_ms", "model_ttft_ms",
                    "model_generation_ms", "tool_ms", "first_chunk_ms", "model_calls"):
            self.assertIn(key, timing, key)
        self.assertGreaterEqual(timing["total_ms"], 0)
        self.assertEqual(timing["model_calls"], 1)
        self.assertEqual(timing["tool_calls"], 0)

    def test_model_route_degrade_note_reaches_model(self):
        """视觉不可用时，route.notes 会贴到当前 user 消息里，让 primary 如实解释。"""
        registry.configure({"tools": {"vision": {"enabled": True}}})
        agent = make_agent(["我无法看图。"], self.tmpdir)
        answer = agent.ask("看看这张图")
        self.assertEqual(answer, "我无法看图。")
        sent = agent.llm.calls[0][-1]["content"]
        self.assertIn("视觉模型不可用", sent)

    def test_untrusted_marker_on_web_result(self):
        from agent import UNTRUSTED_PREFIX
        from tools import registry as reg
        agent = make_agent([], self.tmpdir)
        original = reg.invoke
        reg.invoke = lambda name, arguments, context=None: {
            "ok": True, "url": "https://a", "title": "t", "content": "正文",
            "chars": 2, "untrusted": True}
        try:
            result = agent._run_tool({"name": "web_fetch", "arguments": {"url": "https://a"}})
        finally:
            reg.invoke = original
        self.assertTrue(result["ok"])
        self.assertTrue(result["result"].startswith(UNTRUSTED_PREFIX))

    def test_unavailable_tool_returns_clear_error(self):
        registry.configure({"tools": {"vision": {"enabled": True}}})
        agent = make_agent([], self.tmpdir)
        result = agent._run_tool({"name": "image_analyze", "arguments": {}})
        self.assertFalse(result["ok"])
        self.assertIn("UNAVAILABLE", result["error"])


if __name__ == "__main__":
    unittest.main()
