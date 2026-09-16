# -*- coding: utf-8 -*-
"""Host 测试：HostProfile / ResourceSnapshot / TermuxHost（不需要真的在 Termux 上）。"""

import unittest

from adapter.platform import GenericHost, TermuxHost, detect_host


class HostTest(unittest.TestCase):

    def test_generic_host_profile(self):
        host = GenericHost(workdir=".")
        profile = host.profile()
        self.assertTrue(profile.platform)
        self.assertTrue(profile.architecture)
        self.assertGreaterEqual(profile.ram_bytes, 0)
        self.assertIn("cpu", profile.supported_backends)

    def test_generic_host_snapshot(self):
        snapshot = GenericHost(workdir=".").snapshot()
        data = snapshot.to_dict()
        for key in ("ram_available", "cpu_usage", "temperature", "battery", "timestamp"):
            self.assertIn(key, data)
        if data["cpu_usage"] is not None:
            self.assertGreaterEqual(data["cpu_usage"], 0.0)
            self.assertLessEqual(data["cpu_usage"], 1.0)

    def test_termux_host_does_not_crash_without_api(self):
        host = TermuxHost(workdir=".")
        profile = host.profile()
        snapshot = host.snapshot()
        capabilities = host.capabilities()
        self.assertEqual(profile.platform, "termux")
        self.assertIn("termux-api", profile.supported_backends)
        self.assertIsInstance(capabilities.get("termux"), bool)
        self.assertIsInstance(capabilities.get("termux_api"), bool)
        # 非 Termux 环境下电池/温度应该是 None，而不是抛异常
        if not capabilities["termux_api"]:
            self.assertIsNone(snapshot.battery)

    def test_detect_host_auto_and_explicit(self):
        self.assertIsInstance(detect_host("generic"), GenericHost)
        self.assertIsInstance(detect_host("termux"), TermuxHost)
        self.assertIn(type(detect_host("auto")).__name__, ("GenericHost", "TermuxHost"))

    def test_core_has_no_platform_hardcoding(self):
        """Core 里不允许出现平台/机型的「硬编码判断」（提示词里提到 Termux 无妨）。"""
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        banned = ("Termux", "termux", "Xiaomi", "小米", "Android", "android")
        branching = ("if ", "==", "!=", "import ", ".platform")
        for name in ("agent.py", "llm.py", "tool_parser.py"):
            with open(os.path.join(root, name), "r", encoding="utf-8") as handle:
                text = handle.read()
            for word in banned:
                for line in text.splitlines():
                    if word not in line:
                        continue
                    for token in branching:
                        self.assertNotIn(
                            token, line,
                            "%s 里出现了平台判断：%s" % (name, line.strip()[:80]))
            self.assertNotIn("import platform", text)
            self.assertNotIn("sys.platform", text)
            self.assertNotIn("os.uname", text)


if __name__ == "__main__":
    unittest.main()
