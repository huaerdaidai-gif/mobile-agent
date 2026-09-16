# -*- coding: utf-8 -*-
"""Stage 1 测试：ModelRegistry 与真实能力检测（不伪装可用性）。"""

import unittest

from model.provider import ModelProvider, STATE_READY, STATE_UNAVAILABLE
from model.registry import ModelRegistry
from tests import helpers
from tests.mock_model import MockModelServer


class ModelRegistryTest(unittest.TestCase):

    def setUp(self):
        self.server = MockModelServer(context=2048, reply="ok").start()

    def tearDown(self):
        self.server.stop()

    def registry(self, **model_overrides):
        config = helpers.make_config(self.server.base_url, "/tmp")
        config["models"] = {
            "primary": {"enabled": True},
            "vision": {"enabled": False, "name": "", "endpoint": ""},
            "audio": {"enabled": False, "name": "", "endpoint": ""},
        }
        for role, values in model_overrides.items():
            config["models"][role].update(values)
        return ModelRegistry(config, probe=True)

    def test_primary_is_ready_and_text_only(self):
        info = self.registry().primary().model_info()
        self.assertEqual(info.role, "primary")
        self.assertEqual(info.state, STATE_READY)
        self.assertEqual(info.context, 2048)
        self.assertTrue(self.registry().primary().supports_text())
        self.assertFalse(self.registry().primary().supports_vision())

    def test_vision_and_audio_are_unavailable_by_default(self):
        registry = self.registry()
        vision, audio = registry.vision(), registry.audio()
        self.assertIsInstance(vision, ModelProvider)
        self.assertEqual(vision.model_info().state, STATE_UNAVAILABLE)
        self.assertFalse(vision.supports_vision())
        self.assertFalse(audio.supports_audio())
        self.assertFalse(vision.health()["ok"])

    def test_unavailable_provider_raises_clear_error(self):
        from adapter.model import ModelUnavailableError
        with self.assertRaises(ModelUnavailableError):
            self.registry().vision().chat([{"role": "user", "content": "看图"}])

    def test_capabilities_reflect_reality(self):
        caps = self.registry().capabilities()
        self.assertTrue(caps["primary"]["supports_text"])
        self.assertFalse(caps["primary"]["supports_vision"])
        self.assertFalse(caps["vision"]["supports_vision"])
        self.assertEqual(caps["vision"]["state"], STATE_UNAVAILABLE)

    def test_modalities_are_probed_from_server(self):
        """服务器申报 vision=true 且配置启用时，vision 才可用（不靠猜）。"""
        self.server.modalities = {"vision": True, "audio": False, "video": False}
        registry = self.registry(vision={"enabled": True, "name": "fake-vl",
                                         "endpoint": self.server.base_url})
        self.assertTrue(registry.vision().supports_vision())
        self.assertEqual(registry.vision().model_info().state, STATE_READY)

    def test_vision_enabled_but_server_says_no(self):
        """配置说启用、但服务器 /props 明确 vision=false → 仍然不可用。"""
        registry = self.registry(vision={"enabled": True, "name": "fake-vl",
                                         "endpoint": self.server.base_url})
        self.assertFalse(registry.vision().supports_vision())

    def test_list_models_reports_three_roles(self):
        roles = [item["role"] for item in self.registry().list_models()]
        self.assertEqual(roles, ["primary", "vision", "audio"])

    def test_health_shape(self):
        health = self.registry().health()
        self.assertTrue(health["ok"])
        self.assertIn("primary", health["models"])
        self.assertIn("capabilities", health)

    def test_detect_server_capabilities_helper(self):
        from llm import detect_server_capabilities
        self.server.modalities = {"vision": False, "audio": True, "video": False}
        info = detect_server_capabilities(self.server.base_url)
        self.assertEqual(info["context"], 2048)
        self.assertFalse(info["vision"])
        self.assertTrue(info["audio"])

    def test_capabilities_when_server_down(self):
        registry = ModelRegistry(helpers.make_config("http://127.0.0.1:9/v1", "/tmp"))
        self.assertNotEqual(registry.primary().model_info().state, STATE_READY)
        self.assertFalse(registry.capabilities()["vision"]["supports_vision"])


if __name__ == "__main__":
    unittest.main()
