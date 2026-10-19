# -*- coding: utf-8 -*-
"""MiniCPM native function envelope parsing and dispatch coverage."""

import os
import tempfile
import unittest
from unittest.mock import patch

from agent import Agent
from tests.test_core_regression import StubLLM
from tool_parser import parse_minicpm_function_call
from tools import registry


class MiniCPMNativeToolTest(unittest.TestCase):
    def setUp(self):
        registry.configure({})
        self.tempdir = tempfile.TemporaryDirectory(prefix="ma-minicpm-tool-")

    def tearDown(self):
        registry.configure({})
        self.tempdir.cleanup()

    def make_agent(self, replies):
        return Agent(
            llm=StubLLM(replies), verbose=False, stream=True, max_context=2048,
            server_context=2048, memory_path=os.path.join(self.tempdir.name, "memory.json"),
            memory_enabled=False,
        )

    def test_maps_registered_file_call_with_surrounding_text_and_think(self):
        text = ('我先检查一下。<think>需要读取文件</think>'
                '<function name="file"><param name="action">read</param>'
                '<param name="path">README.md</param></function> 已完成。')
        self.assertEqual(parse_minicpm_function_call(text), {
            "type": "tool_call", "name": "file",
            "arguments": {"action": "read", "path": "README.md"},
        })
        agent = self.make_agent([text, "文件内容已读取。"])
        streamed = []
        with patch.object(agent, "_run_tool", return_value={"ok": True, "result": "内容"}) as run:
            self.assertEqual(agent.ask("请读取 README.md", on_text=streamed.append), "文件内容已读取。")
        run.assert_called_once_with({"type": "tool_call", "name": "file",
                                     "arguments": {"action": "read", "path": "README.md"}})
        self.assertNotIn("<function", "".join(streamed))

    def test_stream_gate_quarantines_xml_after_prose_across_character_chunks(self):
        prose = "我先检查一下。"
        call = ('<think>读取文件</think><function name="file">'
                '<param name="action">read</param><param name="path">README.md</param>'
                '</function>')
        agent = self.make_agent(["unused"])
        chunks = [prose, *list(call)]
        with patch.object(agent, "_stream_with_fallback", side_effect=[iter(chunks),
                                                                         iter(["文件内容已读取。"])]), \
             patch.object(agent, "_run_tool", return_value={"ok": True, "result": "内容"}) as run:
            streamed = []
            self.assertEqual(agent.ask("请读取 README.md", on_text=streamed.append), "文件内容已读取。")
        visible = "".join(streamed)
        self.assertIn(prose, visible)
        self.assertIn("文件内容已读取。", visible)
        self.assertNotIn("<think", visible)
        self.assertNotIn("<function", visible)
        self.assertNotIn("<param", visible)
        run.assert_called_once()

    def test_stream_gate_keeps_ordinary_text_streaming_and_hides_interrupted_xml(self):
        agent = self.make_agent(["unused"])
        chunks_seen = []
        def ordinary_then_interrupt(messages, form):
            yield "普通回答"
            yield "<func"
            raise __import__("llm").LLMError("interrupted")

        with patch.object(agent, "_stream_with_fallback", side_effect=ordinary_then_interrupt):
            answer = agent.ask("你好", on_text=chunks_seen.append)
        self.assertEqual(chunks_seen, ["普通回答"])
        self.assertIn("流式响应中断", answer)

    def test_unknown_tool_and_parameters_are_not_accepted_for_execution(self):
        unknown_tool = '<function name="erase_everything"><param name="path">x</param></function>'
        unknown_param = ('<function name="time"><param name="path">x</param>'
                         '</function>')
        for text in (unknown_tool, unknown_param):
            with self.subTest(text=text):
                agent = self.make_agent([text])
                with patch.object(agent, "_run_tool") as run:
                    self.assertEqual(agent.ask("请读取 README.md"), text)
                run.assert_not_called()

    def test_truncated_function_is_not_executed(self):
        text = ('<function name="file"><param name="action">read</param>'
                '<param name="path">README.md</param>')
        self.assertIsNone(parse_minicpm_function_call(text))
        agent = self.make_agent([text])
        with patch.object(agent, "_run_tool") as run:
            self.assertEqual(agent.ask("请读取 README.md"), text)
        run.assert_not_called()

    def test_duplicate_or_nested_parameter_markup_is_rejected(self):
        duplicate = ('<function name="file"><param name="action">read</param>'
                     '<param name="action">write</param></function>')
        nested = ('<function name="file"><param name="action"><b>read</b></param>'
                  '<param name="path">README.md</param></function>')
        self.assertIsNone(parse_minicpm_function_call(duplicate))
        self.assertIsNone(parse_minicpm_function_call(nested))


if __name__ == "__main__":
    unittest.main()
