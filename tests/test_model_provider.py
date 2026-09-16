# -*- coding: utf-8 -*-
"""ModelProvider 测试：chat / stream / health / 上下文探测（用假模型服务）。"""

import unittest

from tests import helpers
from tests.mock_model import MockModelServer


class ModelProviderTest(unittest.TestCase):

    def setUp(self):
        self.server = MockModelServer(context=2048, reply="你好，我是模拟模型。").start()

    def tearDown(self):
        self.server.stop()

    def provider(self, base_url=None):
        from adapter.model import OpenAICompatibleProvider
        return OpenAICompatibleProvider(base_url=base_url or self.server.base_url,
                                        name="mock-model", timeout=10, max_output_tokens=64)

    def test_chat_returns_text(self):
        provider = self.provider()
        messages = [{"role": "user", "content": "你好"}]
        self.assertEqual(provider.chat(messages), "你好，我是模拟模型。")

    def test_stream_yields_chunks(self):
        provider = self.provider()
        chunks = list(provider.stream([{"role": "user", "content": "你好"}]))
        self.assertGreater(len(chunks), 1, "流式应该分多段返回")
        self.assertEqual("".join(chunks), "你好，我是模拟模型。")

    def test_health_reports_context(self):
        provider = self.provider()
        health = provider.health()
        self.assertTrue(health["ok"], health)
        self.assertEqual(health["context"], 2048)
        self.assertEqual(provider.profile().context, 2048)

    def test_health_fails_when_server_down(self):
        provider = self.provider(base_url="http://127.0.0.1:9/v1")
        health = provider.health()
        self.assertFalse(health["ok"])
        self.assertIsNotNone(health["error"])

    def test_health_fails_on_http_error(self):
        self.server.fail = True
        health = self.provider().health()
        self.assertFalse(health["ok"])

    def test_build_provider_uses_model_section_override(self):
        from adapter.model import build_provider
        config = helpers.make_config(self.server.base_url, "/tmp")
        config["model"] = {"provider": "openai-compatible",
                           "base_url": self.server.base_url, "name": "override-name"}
        provider = build_provider(config)
        self.assertEqual(provider.model_name, "override-name")
        self.assertEqual(provider.max_output_tokens, 256)

    def test_last_metrics_collected(self):
        provider = self.provider()
        provider.chat([{"role": "user", "content": "你好"}])
        metrics = provider.last_metrics()
        self.assertEqual(metrics["usage"].get("prompt_tokens"), 42)
        self.assertEqual(metrics["timings"].get("prompt_n"), 12)


if __name__ == "__main__":
    unittest.main()
