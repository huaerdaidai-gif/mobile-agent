# -*- coding: utf-8 -*-
"""健康检查测试：健康/异常两种情况 + 文本渲染。"""

import tempfile
import unittest

from gateway import health as gateway_health
from tests import helpers
from tests.mock_model import MockModelServer
from tests.mock_onebot import MockOneBotServer


class HealthTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ma-health-")
        self.model = MockModelServer(context=2048, reply="ok").start()
        self.onebot = MockOneBotServer().start()

    def tearDown(self):
        self.model.stop()
        self.onebot.stop()

    def service_for(self, base_url):
        from runtime.service import AgentService
        config = helpers.make_config(base_url, self.tmpdir)
        config["qq"]["enabled"] = True
        config["qq"]["api_base"] = self.onebot.api_base
        return AgentService(config=config)

    def test_health_all_ok(self):
        service = self.service_for(self.model.base_url)
        payload = gateway_health.collect(service, gateway_state={"listening": True,
                                                                 "host": "127.0.0.1", "port": 8787})
        self.assertTrue(payload["ok"], payload)
        self.assertTrue(payload["components"]["model"]["ok"])
        self.assertTrue(payload["components"]["qq"]["ok"])
        self.assertTrue(payload["components"]["host"]["ok"])
        # Stage 3：三项独立探测由调度器并行执行，并给出每项耗时
        self.assertIn("probe_ms", payload)
        self.assertIn("model", payload["probe_ms"])
        # Stage 2：可用工具数量由分组开关决定（core/system/web/writing 默认启用）
        from tools import registry
        self.assertEqual(payload["components"]["capability"]["count"],
                         len(registry.tool_names()))
        self.assertGreaterEqual(payload["components"]["capability"]["count"], 3)

    def test_health_reports_model_failure(self):
        service = self.service_for("http://127.0.0.1:9/v1")
        payload = gateway_health.collect(service, gateway_state={"listening": True})
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["components"]["model"]["ok"])
        self.assertIsNotNone(payload["components"]["model"]["error"])

    def test_health_reports_qq_failure(self):
        from runtime.service import AgentService
        config = helpers.make_config(self.model.base_url, self.tmpdir)
        config["qq"]["enabled"] = True
        config["qq"]["api_base"] = "http://127.0.0.1:9"   # 打不通
        service = AgentService(config=config)
        payload = gateway_health.collect(service, gateway_state={"listening": True})
        self.assertFalse(payload["components"]["qq"]["ok"])
        self.assertFalse(payload["ok"])

    def test_render_text_contains_components(self):
        service = self.service_for(self.model.base_url)
        payload = gateway_health.collect(service, gateway_state={"listening": True})
        text = gateway_health.render_text(payload)
        for name in ("总体", "agent", "model", "gateway", "qq", "host", "capability"):
            self.assertIn(name, text)

    def test_fast_mode_skips_model_probe(self):
        service = self.service_for(self.model.base_url)
        payload = gateway_health.collect(service, gateway_state={"listening": True},
                                         model_probe=False)
        model = payload["components"]["model"]
        self.assertTrue(model["ok"])
        self.assertFalse(model.get("checked", True))


if __name__ == "__main__":
    unittest.main()
