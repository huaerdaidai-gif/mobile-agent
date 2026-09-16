# -*- coding: utf-8 -*-
"""PHASE 3.6：QQ → Vision 透传的端到端测试（全部离线）。

链路：
    OneBot 事件 → QQBotAdapter.parse_event → ChannelMessage.attachments
    → handle() → service.ask(..., attachments=...)

其中「真实链路」用例使用项目自带的假模型服务（tests/mock_model.py，仅监听 127.0.0.1），
不启动 llama.cpp、不访问外网、不下载任何内容。
"""

import os
import tempfile
import unittest

from adapter.qq import QQBotAdapter
from adapter.qq.onebot import DEFAULT_IMAGE_TEXT
from tests import helpers
from tests.mock_model import MockModelServer


def make_config(**qq_overrides):
    qq = {"enabled": True, "adapter": "onebot", "api_base": "http://127.0.0.1:1",
          "token": "", "self_id": "10001", "require_at": True, "reply_group": True,
          "at_sender": False, "max_chars": 1200, "allow_users": ""}
    qq.update(qq_overrides)
    return {"qq": qq}


def image_segment(url="", file=""):
    data = {}
    if url:
        data["url"] = url
    if file:
        data["file"] = file
    return {"type": "image", "data": data}


def private_event(segments, text=None, user_id=20002):
    return {"post_type": "message", "message_type": "private", "user_id": user_id,
            "self_id": 10001, "raw_message": text or "", "message": segments}


class RecordingService(object):
    """假的 AgentService：记录 text / session_id / attachments，返回固定回复。"""

    def __init__(self, reply="QQ 回复。"):
        self.reply = reply
        self.calls = []

    def ask(self, text, session_id="default", on_text=None, attachments=None):
        self.calls.append({"text": text, "session_id": session_id, "attachments": attachments})
        if on_text:
            on_text(self.reply)
        return self.reply


class QQVisionPassthroughTest(unittest.TestCase):
    """不涉及任何 socket：只验证解析 + 透传。"""

    def setUp(self):
        self.adapter = QQBotAdapter(make_config())
        # 发送动作在本文件里不需要真实 HTTP（发送逻辑已在 test_qq_adapter.py 覆盖）
        self.sent = []
        self.adapter.send = lambda reply_to, text: self.sent.append((reply_to, text)) or {"ok": True}

    # ---- 1. 文字 + 图片 ----

    def test_text_plus_image_keeps_text_and_passes_images(self):
        service = RecordingService()
        self.adapter.handle(private_event(
            [{"type": "text", "data": {"text": "请描述这张图"}},
             image_segment(url="http://example.com/a.jpg")], text="请描述这张图"), service)
        call = service.calls[0]
        self.assertEqual(call["text"], "请描述这张图")                  # 文字原样
        self.assertEqual(call["attachments"], {"images": ["http://example.com/a.jpg"]})
        self.assertEqual(call["session_id"], "qq:private:20002")
        self.assertEqual(len(self.sent), 1)                            # 回复照常发出

    # ---- 2. 纯图片 ----

    def test_image_only_uses_default_question(self):
        service = RecordingService()
        self.adapter.handle(private_event([image_segment(file="only.jpg")]), service)
        call = service.calls[0]
        self.assertEqual(call["text"], DEFAULT_IMAGE_TEXT)
        self.assertEqual(call["attachments"], {"images": ["only.jpg"]})

    # ---- 3. url / file 优先级 ----

    def test_url_is_passed_when_present(self):
        service = RecordingService()
        self.adapter.handle(private_event(
            [image_segment(url="http://example.com/a.jpg", file="local.jpg")]), service)
        self.assertEqual(service.calls[0]["attachments"],
                         {"images": ["http://example.com/a.jpg"]})

    def test_file_is_passed_when_url_missing(self):
        service = RecordingService()
        self.adapter.handle(private_event([image_segment(file="local.jpg")]), service)
        self.assertEqual(service.calls[0]["attachments"], {"images": ["local.jpg"]})

    # ---- 4. 多图片：顺序不变、QQ 层不截断 ----

    def test_multiple_images_are_not_truncated(self):
        service = RecordingService()
        self.adapter.handle(private_event([
            image_segment(url="http://example.com/1.jpg"),
            image_segment(file="2.jpg"),
            image_segment(url="http://example.com/3.jpg"),
        ]), service)
        self.assertEqual(service.calls[0]["attachments"],
                         {"images": ["http://example.com/1.jpg", "2.jpg",
                                     "http://example.com/3.jpg"]})

    # ---- 5. 无有效文字且无有效图片 ----

    def test_no_text_and_no_image_is_still_dropped(self):
        self.assertIsNone(self.adapter.parse_event(private_event([image_segment()])))
        self.assertIsNone(self.adapter.parse_event(
            private_event([{"type": "text", "data": {"text": "   "}}])))

    # ---- 6. 旧文字消息 ----

    def test_legacy_text_message_unchanged(self):
        service = RecordingService()
        message = self.adapter.parse_event(private_event(
            [{"type": "text", "data": {"text": "你好"}}], text="你好"))
        self.assertEqual(message.text, "你好")
        self.assertEqual(message.attachments, {})
        self.assertEqual(message.session_id, "qq:private:20002")
        self.adapter.handle(private_event(
            [{"type": "text", "data": {"text": "你好"}}], text="你好"), service)
        self.assertEqual(service.calls[0]["attachments"], {})
        self.assertEqual(service.calls[0]["text"], "你好")


class QQVisionEndToEndTest(unittest.TestCase):
    """真实链路：QQ 事件 → 真 AgentService → Router → Vision provider → Primary。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ma-qq-vision-")
        self.primary = MockModelServer(reply="这是主模型的回答。").start()

    def tearDown(self):
        self.primary.stop()
        if getattr(self, "vision", None):
            self.vision.stop()

    def build(self, with_vision=True):
        config = helpers.make_config(self.primary.base_url, self.tmpdir)
        if with_vision:
            self.vision = MockModelServer(
                reply="图里是一只橘猫。",
                modalities={"vision": True, "audio": False, "video": False}).start()
            config["models"] = {
                "vision": {"enabled": True, "name": "mock-vision",
                           "endpoint": self.vision.base_url, "timeout": 30,
                           "max_output_tokens": 256},
                "audio": {"enabled": False},
            }
        else:
            config["models"] = {"vision": {"enabled": False}, "audio": {"enabled": False}}
        from runtime.service import AgentService
        service = AgentService(config=config, auto_probe=True)
        adapter = QQBotAdapter(make_config())
        sent = []
        adapter.send = lambda reply_to, text: sent.append((reply_to, text)) or {"ok": True}
        return service, adapter, sent

    def test_qq_image_reaches_vision_provider(self):
        service, adapter, sent = self.build(with_vision=True)
        reply = adapter.handle(private_event(
            [{"type": "text", "data": {"text": "这是什么？"}},
             image_segment(url="http://example.com/qq.jpg")], text="这是什么？"), service)
        self.assertEqual(reply, "这是主模型的回答。")
        # 视觉模型被调用一次，且拿到的正是 QQ 事件里的图片地址
        self.assertEqual(len(self.vision.calls), 1)
        payload = self.vision.calls[0]["messages"][0]["content"]
        self.assertEqual(payload[1]["type"], "image_url")
        self.assertEqual(payload[1]["image_url"]["url"], "http://example.com/qq.jpg")
        self.assertIn("这是什么？", payload[0]["text"])
        # Primary 收到视觉摘要，但看不到图片本身；history 保持纯文本
        primary_prompt = str(self.primary.calls[-1]["messages"])
        self.assertIn("图片理解结果", primary_prompt)
        self.assertNotIn("image_url", primary_prompt)
        agent = service.sessions.get("qq:private:20002").agent
        self.assertTrue(all(isinstance(m.get("content"), str) for m in agent.history))
        # 回复真的发回了 QQ
        self.assertEqual(sent[0][1], "这是主模型的回答。")
        self.assertEqual(sent[0][0], {"type": "private", "id": "20002"})

    def test_qq_image_only_uses_default_question_end_to_end(self):
        service, adapter, _sent = self.build(with_vision=True)
        adapter.handle(private_event([image_segment(file="only.jpg")]), service)
        self.assertEqual(len(self.vision.calls), 1)
        self.assertIn(DEFAULT_IMAGE_TEXT,
                      self.vision.calls[0]["messages"][0]["content"][0]["text"])

    def test_qq_text_only_never_calls_vision(self):
        service, adapter, _sent = self.build(with_vision=True)
        adapter.handle(private_event(
            [{"type": "text", "data": {"text": "你好"}}], text="你好"), service)
        self.assertEqual(len(self.vision.calls), 0)
        self.assertEqual(len(self.primary.calls), 1)

    def test_qq_image_degrades_when_vision_unavailable(self):
        service, adapter, sent = self.build(with_vision=False)
        reply = adapter.handle(private_event(
            [{"type": "text", "data": {"text": "这是什么？"}},
             image_segment(url="http://example.com/qq.jpg")], text="这是什么？"), service)
        self.assertTrue(reply)
        prompt = str(self.primary.calls[-1]["messages"])
        self.assertIn("视觉模型不可用", prompt)          # 明确告知，不伪造
        self.assertNotIn("图片理解结果", prompt)
        self.assertEqual(sent[0][1], reply)


if __name__ == "__main__":
    unittest.main()
