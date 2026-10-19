# -*- coding: utf-8 -*-
"""Owner-only model API key file and authenticated request coverage."""

import io
import logging
import os
import tempfile
import unittest
from unittest import mock

import llm as llm_module
from llm import LLM, LLMError, detect_server_capabilities, detect_server_context, load_api_key_file
from tests.mock_model import MockModelServer


class LLMApiKeyTest(unittest.TestCase):
    def setUp(self):
        self.server = MockModelServer(context=2048, reply="authenticated reply").start()
        handle, self.key_path = tempfile.mkstemp(prefix="ma-key-")
        os.fchmod(handle, 0o600)
        os.write(handle, b"  test-secret-key  \n")
        os.close(handle)
        self.key = "test-secret-key"

    def tearDown(self):
        self.server.stop()
        try:
            os.unlink(self.key_path)
        except OSError:
            pass

    def test_key_file_is_trimmed_and_only_accepts_private_modes(self):
        self.assertEqual(load_api_key_file(self.key_path), self.key)
        os.chmod(self.key_path, 0o400)
        self.assertEqual(load_api_key_file(self.key_path), self.key)
        os.chmod(self.key_path, 0o600)
        with open(self.key_path, "w", encoding="utf-8") as handle:
            handle.write("  \n")
        self.assertEqual(load_api_key_file(self.key_path), "")
        with open(self.key_path, "w", encoding="utf-8") as handle:
            handle.write(self.key)
        os.chmod(self.key_path, 0o644)
        with self.assertRaisesRegex(ValueError, "permissions must be 400 or 600"):
            load_api_key_file(self.key_path)
        self.assertEqual(load_api_key_file(""), "")

    def test_key_file_must_be_owned_by_current_user_and_not_a_symlink(self):
        with mock.patch.object(llm_module.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaisesRegex(ValueError, "owned by the current user"):
                load_api_key_file(self.key_path)
        link = self.key_path + ".link"
        try:
            os.symlink(self.key_path, link)
            with self.assertRaisesRegex(ValueError, "symbolic link|cannot be opened"):
                load_api_key_file(link)
        finally:
            try:
                os.unlink(link)
            except OSError:
                pass

    def test_key_is_sent_for_chat_stream_and_model_probes(self):
        client = LLM(base_url=self.server.base_url, api_key=self.key, timeout=5)
        self.assertEqual(client.chat([{"role": "user", "content": "hello"}]),
                         "authenticated reply")
        self.assertEqual("".join(client.chat_stream([{"role": "user", "content": "hello"}])),
                         "authenticated reply")
        self.assertEqual(detect_server_context(self.server.base_url, api_key=self.key)["context"], 2048)
        detect_server_capabilities(self.server.base_url, api_key=self.key)
        self.assertTrue(self.server.request_headers)
        for _path, authorization in self.server.request_headers:
            self.assertEqual(authorization, "Bearer " + self.key)
        paths = [path for path, _auth in self.server.request_headers]
        self.assertGreaterEqual(paths.count("/v1/chat/completions"), 2)
        self.assertIn("/v1/models", paths)
        self.assertIn("/props", paths)

    def test_urllib_fallback_sends_authorization_for_all_model_requests(self):
        self.server.request_headers.clear()
        with mock.patch.object(llm_module, "_HAS_REQUESTS", False):
            client = LLM(base_url=self.server.base_url, api_key=self.key, timeout=5)
            client.chat([{"role": "user", "content": "hello"}])
            list(client.chat_stream([{"role": "user", "content": "hello"}]))
            detect_server_context(self.server.base_url, api_key=self.key)
            detect_server_capabilities(self.server.base_url, api_key=self.key)
        self.assertTrue(self.server.request_headers)
        self.assertTrue(all(auth == "Bearer " + self.key
                            for _path, auth in self.server.request_headers))

    def test_unconfigured_key_preserves_requests_without_authorization(self):
        client = LLM(base_url=self.server.base_url, timeout=5)
        client.chat([{"role": "user", "content": "hello"}])
        list(client.chat_stream([{"role": "user", "content": "hello"}]))
        detect_server_context(self.server.base_url)
        detect_server_capabilities(self.server.base_url)
        self.assertTrue(self.server.request_headers)
        self.assertTrue(all(auth is None for _path, auth in self.server.request_headers))

    def test_key_is_removed_from_errors_and_model_output(self):
        client = LLM(base_url=self.server.base_url, api_key=self.key, timeout=5)
        self.server.reply = self.key
        self.assertNotIn(self.key, client.chat([{"role": "user", "content": "hello"}]))
        streamed = "".join(client.chat_stream([{"role": "user", "content": "hello"}]))
        self.assertNotIn(self.key, streamed)
        self.server.fail = True
        self.server.error_message = "upstream reflected " + self.key
        with self.assertRaises(LLMError) as caught:
            client.chat([{"role": "user", "content": "hello"}])
        self.assertNotIn(self.key, str(caught.exception))
        from runtime import logging as agent_log
        logger = logging.getLogger("mobile-agent")
        captured = io.StringIO()
        handler = logging.StreamHandler(captured)
        logger.addHandler(handler)
        try:
            agent_log.log("MODEL", "model request failed: %s" % caught.exception,
                          level="ERROR")
        finally:
            logger.removeHandler(handler)
        self.assertNotIn(self.key, captured.getvalue())

    def test_build_agent_uses_key_for_startup_context_probe(self):
        from main import build_agent
        from tests import helpers
        with tempfile.TemporaryDirectory(prefix="ma-build-") as directory:
            config = helpers.make_config(self.server.base_url, directory)
            config["llm"]["api_key_file"] = self.key_path
            agent = build_agent(config, verbose=False)
            self.assertEqual(agent.server_info["context"], 2048)
            self.assertEqual(self.server.request_headers[-1][1], "Bearer " + self.key)
            self.assertNotIn(self.key, repr(agent.server_info))

    def test_runtime_provider_uses_key_file_for_health_probe(self):
        from adapter.model import build_provider
        from tests import helpers
        config = helpers.make_config(self.server.base_url, tempfile.gettempdir())
        config["llm"]["api_key_file"] = self.key_path
        provider = build_provider(config)
        health = provider.health()
        self.assertTrue(health["ok"], health)
        self.assertEqual(self.server.request_headers[-1][1], "Bearer " + self.key)
        self.assertNotIn(self.key, repr(health))

    def test_runtime_provider_accepts_key_file_environment_override(self):
        from adapter.model import build_provider
        from tests import helpers
        config = helpers.make_config(self.server.base_url, tempfile.gettempdir())
        with mock.patch.dict(os.environ, {"MOBILE_AGENT_LLM_API_KEY_FILE": self.key_path}):
            provider = build_provider(config)
            self.assertTrue(provider.health()["ok"])
        self.assertEqual(self.server.request_headers[-1][1], "Bearer " + self.key)


if __name__ == "__main__":
    unittest.main()
