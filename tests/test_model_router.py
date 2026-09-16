# -*- coding: utf-8 -*-
"""Stage 1 测试：LightweightModelRouter 的确定性路由与降级。"""

import unittest

from model.router import (ROUTE_AUDIO_TO_TEXT, ROUTE_MULTIMODAL, ROUTE_TEXT, ROUTE_VISION,
                          ROUTE_VISION_TO_TEXT, LightweightModelRouter)


class FakeProvider(object):
    """只有能力的最小 Provider 替身。"""

    def __init__(self, vision=False, audio=False, text=True):
        self._vision, self._audio, self._text = vision, audio, text

    def supports_text(self):
        return self._text

    def supports_vision(self):
        return self._vision

    def supports_audio(self):
        return self._audio


class FakeRegistry(object):
    def __init__(self, vision=False, audio=False):
        self._providers = {"primary": FakeProvider(),
                           "vision": FakeProvider(vision=vision, text=False),
                           "audio": FakeProvider(audio=audio, text=False)}

    def get(self, role):
        return self._providers[role]


class ModelRouterTest(unittest.TestCase):

    def router(self, vision=False, audio=False):
        return LightweightModelRouter(registry=FakeRegistry(vision=vision, audio=audio))

    def test_plain_chat_goes_to_primary_only(self):
        route = self.router().route("你好，介绍一下你自己")
        self.assertEqual(route.route, ROUTE_TEXT)
        self.assertEqual(route.steps, ["primary"])
        self.assertFalse(route.degraded)

    def test_chat_words_do_not_trigger_media_routes(self):
        for text in ("看看这个", "查查资料吧", "你知道吗", "今天心情怎么样", "帮我看看这段代码"):
            route = self.router().route(text)
            self.assertEqual(route.route, ROUTE_TEXT, text)
            self.assertEqual(route.steps, ["primary"], text)

    def test_image_text_routes_to_vision_when_available(self):
        route = self.router(vision=True).route("看看这张图说明了什么")
        self.assertIn(route.route, (ROUTE_VISION, ROUTE_VISION_TO_TEXT))
        self.assertEqual(route.steps[0], "vision")

    def test_image_plus_reasoning_routes_vision_then_primary(self):
        route = self.router(vision=True).route("看看这张图，告诉我怎么操作")
        self.assertEqual(route.route, ROUTE_VISION_TO_TEXT)
        self.assertEqual(route.steps, ["vision", "primary"])

    def test_image_attachment_routes_to_vision(self):
        route = self.router(vision=True).route("这是什么", attachments={"images": ["a.jpg"]})
        self.assertEqual(route.steps[0], "vision")

    def test_audio_routes(self):
        router = self.router(audio=True)
        self.assertEqual(router.route("听一下这段录音").steps[0], "audio")
        self.assertEqual(router.route("这段音频说了什么，帮我总结").route, ROUTE_AUDIO_TO_TEXT)

    def test_multimodal_when_both_available(self):
        route = self.router(vision=True, audio=True).route(
            "看看这张图，再听一下这段录音",
            attachments={"images": ["a.jpg"], "audios": ["a.wav"]})
        self.assertEqual(route.route, ROUTE_MULTIMODAL)
        self.assertEqual(route.steps, ["vision", "audio", "primary"])

    def test_vision_unavailable_degrades_to_primary_with_note(self):
        route = self.router(vision=False).route("看看这张图")
        self.assertEqual(route.route, ROUTE_TEXT)
        self.assertTrue(route.degraded)
        self.assertEqual(route.steps, ["primary"])
        self.assertIn("UNAVAILABLE", route.notes[0])

    def test_audio_unavailable_degrades_to_primary_with_note(self):
        route = self.router(audio=False).route("听一下这段录音")
        self.assertTrue(route.degraded)
        self.assertIn("音频模型不可用", route.notes[0])

    def test_router_uses_registry_capability_not_flags(self):
        """registry 说不可用，就不能用 flag 假装可用。"""
        router = LightweightModelRouter(registry=FakeRegistry(vision=False), vision_available=True)
        self.assertFalse(router.vision_available())
        self.assertTrue(router.route("看看这张图").degraded)

    def test_route_to_dict(self):
        data = self.router().route("你好").to_dict()
        self.assertEqual(sorted(data.keys()), ["degraded", "notes", "reason", "route", "steps"])


if __name__ == "__main__":
    unittest.main()
