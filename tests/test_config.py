# -*- coding: utf-8 -*-
"""配置测试：默认值、两层 YAML 解析、环境变量展开、优先级与告警。"""

import os
import tempfile
import unittest

from tests import helpers
from runtime import config as runtime_config

CONFIG_TEXT = """
stream: true

llm:
  base_url: http://127.0.0.1:9999/v1
  model: file-model
  temperature: 0.3
  timeout: 60

agent:
  max_context: 1024

performance:
  tool_result_max_chars: 1234

model:
  provider: openai-compatible
  name: override-model

gateway:
  host: 0.0.0.0
  port: 9999
  auth: token
  token: ${MA_TEST_TOKEN}

qq:
  enabled: true
  api_base: http://127.0.0.1:3000
  token: ${MA_TEST_QQ_TOKEN:-fallback-token}
"""


class ConfigTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ma-cfg-")
        self.path = os.path.join(self.tmpdir, "config.yaml")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(CONFIG_TEXT)

    def test_two_level_yaml_and_defaults(self):
        os.environ["MA_TEST_TOKEN"] = "secret-token"
        config = runtime_config.load(self.path)
        self.assertEqual(config["gateway"]["host"], "0.0.0.0")
        self.assertEqual(config["gateway"]["port"], 9999)
        # 未在文件里出现的键要有默认值
        self.assertEqual(config["gateway"]["max_sessions"], 16)
        self.assertEqual(config["logging"]["level"], "INFO")
        self.assertEqual(runtime_config.context_limit(config), 1024)
        self.assertEqual(runtime_config.tool_limit(config), 1234)

    def test_env_expansion_and_default(self):
        os.environ["MA_TEST_TOKEN"] = "secret-token"
        os.environ.pop("MA_TEST_QQ_TOKEN", None)
        config = runtime_config.load(self.path)
        self.assertEqual(config["gateway"]["token"], "secret-token")
        self.assertEqual(config["qq"]["token"], "fallback-token")

    def test_model_settings_precedence(self):
        config = runtime_config.load(self.path)
        settings = runtime_config.model_settings(config)
        self.assertEqual(settings["name"], "override-model")          # model.* 覆盖 llm.*
        self.assertEqual(settings["base_url"], "http://127.0.0.1:9999/v1")  # 回落 llm.*
        self.assertEqual(settings["timeout"], 60)
        self.assertEqual(settings["max_output_tokens"], 512)

    def test_warnings_detect_missing_token(self):
        os.environ.pop("MA_TEST_TOKEN", None)
        os.environ.pop("MA_TEST_QQ_TOKEN", None)
        config = runtime_config.load(self.path)
        issues = runtime_config.warnings(config)
        # gateway.auth=token 但 ${MA_TEST_TOKEN} 未设置 → 应该有告警
        self.assertTrue(any("gateway.token" in item for item in issues), issues)
        # qq.token 有默认值，因此不应触发告警
        self.assertFalse(any("qq.token" in item for item in issues), issues)

    def test_get_helper(self):
        config = runtime_config.load(self.path)
        self.assertEqual(runtime_config.get(config, "gateway.port"), 9999)
        self.assertEqual(runtime_config.get(config, "gateway.missing", "default"), "default")

    def test_test_helper_config_is_isolated(self):
        config = helpers.make_config("http://127.0.0.1:1/v1", self.tmpdir)
        self.assertEqual(config["llm"]["model"], "mock-model")
        self.assertTrue(config["_memory_path"].endswith("memory.json"))


if __name__ == "__main__":
    unittest.main()
