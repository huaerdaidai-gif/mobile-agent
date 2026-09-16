# -*- coding: utf-8 -*-
"""Stage 2 测试：工具分组、按需 Schema、可用性判定、统一 invoke 与结果格式化。"""

import os
import tempfile
import unittest

from tests import helpers
from tools import registry


class ToolGroupsTest(unittest.TestCase):

    def setUp(self):
        # 每个测试前恢复默认配置（避免互相影响）
        registry.configure({})

    def tearDown(self):
        registry.configure({})

    # ---- 分组与可用性 ----

    def test_default_enabled_groups(self):
        for group in ("core", "system", "web", "writing"):
            self.assertTrue(registry.group_enabled(group), group)
        for group in ("vision", "audio", "media"):
            self.assertFalse(registry.group_enabled(group), group)

    def test_available_tools_match_reality(self):
        for name in ("time", "shell", "file", "weather", "web_search", "web_fetch",
                     "document_write"):
            self.assertTrue(registry.tool_available(name), name)
        for name in ("image_analyze", "speech_to_text", "text_to_speech",
                     "music_search", "music_play"):
            self.assertFalse(registry.tool_available(name), name)

    def test_disabled_group_hides_tools(self):
        registry.configure({"tools": {"web": {"enabled": False}}})
        self.assertFalse(registry.group_enabled("web"))
        self.assertFalse(registry.tool_available("weather"))
        self.assertNotIn("weather", registry.tool_names())

    def test_enabling_vision_group_only_helps_model_free_tools(self):
        registry.configure({"tools": {"vision": {"enabled": True}}})
        self.assertTrue(registry.tool_available("image_info"))
        self.assertTrue(registry.tool_available("image_download"))
        # 没有视觉模型 → 理解图片仍然不可用
        self.assertFalse(registry.tool_available("image_analyze"))

    def test_vision_tools_become_available_with_capability(self):
        registry.configure({"tools": {"vision": {"enabled": True}}},
                           capabilities={"vision": True})
        self.assertTrue(registry.tool_available("image_analyze"))

    # ---- Schema 按需 + 缓存 ----

    def test_schema_only_contains_requested_groups(self):
        core_schema = registry.schema_text(["core"])
        self.assertIn("time", core_schema)
        self.assertNotIn("web_search", core_schema)
        web_schema = registry.schema_text(["web"])
        self.assertIn("web_search", web_schema)
        self.assertNotIn("- time", web_schema)

    def test_schema_empty_for_plain_chat(self):
        self.assertEqual(registry.schema_text([]), "")

    def test_schema_is_cached(self):
        before = registry.schema_cache_info()["entries"]
        first = registry.schema_text(["core"])
        second = registry.schema_text(["core"])
        self.assertIs(first, second, "同一组应当直接命中缓存")
        self.assertLessEqual(registry.schema_cache_info()["entries"], before + 1)

    def test_schema_excludes_unavailable_tools(self):
        registry.configure({"tools": {"vision": {"enabled": True}}})
        schema = registry.schema_text(["vision"])
        self.assertIn("image_info", schema)
        self.assertNotIn("image_analyze", schema)

    # ---- 参数校验 ----

    def test_validate_missing_required(self):
        ok, error = registry.validate_arguments("web_search", {})
        self.assertFalse(ok)
        self.assertIn("缺少必填参数", error)

    def test_validate_unknown_argument(self):
        ok, error = registry.validate_arguments("time", {"x": "1"})
        self.assertFalse(ok)
        self.assertIn("不支持的参数", error)

    def test_validate_unavailable_tool_explains(self):
        registry.configure({"tools": {"vision": {"enabled": True}}})   # 组开了但模型没有
        ok, error = registry.validate_arguments("image_analyze", {})
        self.assertFalse(ok)
        self.assertIn("UNAVAILABLE", error)

    def test_validate_disabled_group_explains(self):
        registry.configure({"tools": {"web": {"enabled": False}}})
        ok, error = registry.validate_arguments("web_search", {"query": "x"})
        self.assertFalse(ok)
        self.assertIn("已关闭", error)

    # ---- 统一执行 ----

    def test_invoke_time(self):
        raw = registry.invoke("time", {})
        self.assertTrue(raw["ok"])
        self.assertIn("datetime", raw)

    def test_invoke_unknown_tool(self):
        raw = registry.invoke("nope", {})
        self.assertFalse(raw["ok"])
        self.assertIn("没有名为 nope 的工具", raw["error"])

    def test_invoke_unavailable_tool(self):
        registry.configure({"tools": {"vision": {"enabled": True}}})  # 组开启但模型缺失
        raw = registry.invoke("image_analyze", {})
        self.assertFalse(raw["ok"])
        self.assertIn("UNAVAILABLE", raw["error"])

    def test_invoke_document_write_stays_inside_project(self):
        raw = registry.invoke("document_write", {"path": "../../evil.md", "content": "x"})
        self.assertFalse(raw["ok"])
        self.assertIn("路径越界", raw["error"])

    def test_invoke_is_exception_safe(self):
        raw = registry.invoke("shell", {"command": "ls"})
        self.assertIn("ok", raw)

    # ---- 结果格式化 ----

    def test_format_time_and_shell(self):
        time_result = registry.format_result("time", {}, registry.invoke("time", {}))
        self.assertTrue(time_result["ok"])
        self.assertIn("当前时间", time_result["result"])
        shell_result = registry.format_result("shell", {"command": "ls"},
                                              registry.invoke("shell", {"command": "ls"}))
        self.assertIn("退出码", shell_result["result"])

    def test_format_weather(self):
        raw = {"ok": True, "location": "北京 中国", "weather": "晴", "temperature": "25 °C",
               "feels_like": "24 °C", "humidity": "40%", "wind": "2 m/s",
               "observed_at": "2026-09-16T18:00", "source": "open-meteo"}
        text = registry.format_result("weather", {}, raw)["result"]
        self.assertIn("北京 中国", text)
        self.assertIn("晴", text)

    def test_format_search_and_untrusted_flag(self):
        raw = {"ok": True, "count": 1, "results": [{"title": "标题", "url": "https://a",
                                                    "snippet": "摘要"}]}
        text = registry.format_result("web_search", {}, raw)["result"]
        self.assertIn("https://a", text)
        self.assertTrue(registry.is_untrusted("web_search"))
        self.assertTrue(registry.is_untrusted("web_fetch"))
        self.assertFalse(registry.is_untrusted("time"))

    def test_format_error_result(self):
        out = registry.format_result("weather", {}, {"ok": False, "error": "boom"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "boom")

    # ---- 与 v0.25.1 兼容 ----

    def test_legacy_helpers_still_work(self):
        self.assertIn("time", registry.get_tools())
        self.assertIsNotNone(registry.get_tool("file"))
        self.assertIn("command", registry.parameter_names())
        self.assertIn("当前工具", registry.describe_tools())
        self.assertTrue(registry.example_arguments("weather"))

    def test_availability_report_shape(self):
        report = registry.availability_report()
        self.assertIn("groups", report)
        self.assertIn("tools", report)
        self.assertFalse(report["tools"]["image_analyze"]["available"])


if __name__ == "__main__":
    unittest.main()
