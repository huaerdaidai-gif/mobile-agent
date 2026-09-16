# -*- coding: utf-8 -*-
"""QQ 适配器测试：事件解析、白名单、分片发送、完整 handle 流程。"""

import unittest

from adapter.qq import QQBotAdapter
from adapter.qq.onebot import DEFAULT_IMAGE_TEXT
from tests.mock_onebot import MockOneBotServer


def make_config(api_base: str, **qq_overrides):
    qq = {"enabled": True, "adapter": "onebot", "api_base": api_base, "token": "",
          "self_id": "10001", "require_at": True, "reply_group": True,
          "at_sender": False, "max_chars": 1200, "allow_users": ""}
    qq.update(qq_overrides)
    return {"qq": qq}


def private_event(text="你好", user_id=20002):
    return {"post_type": "message", "message_type": "private", "user_id": user_id,
            "self_id": 10001, "raw_message": text,
            "message": [{"type": "text", "data": {"text": text}}]}


def group_event(text="你好", user_id=20002, group_id=30003, at_bot=True):
    segments = []
    if at_bot:
        segments.append({"type": "at", "data": {"qq": "10001"}})
    segments.append({"type": "text", "data": {"text": text}})
    return {"post_type": "message", "message_type": "group", "user_id": user_id,
            "group_id": group_id, "self_id": 10001, "raw_message": text, "message": segments}


def image_segment(url="", file=""):
    """造一个 OneBot image 段；只填传入的字段（模拟真实实现缺字段的情况）。"""
    data = {}
    if url:
        data["url"] = url
    if file:
        data["file"] = file
    return {"type": "image", "data": data}


def private_image_event(segments, text=None, raw_message="", user_id=20002):
    """私聊图片事件：segments 是完整的消息段列表，text 只用于 raw_message 兼容字段。"""
    return {"post_type": "message", "message_type": "private", "user_id": user_id,
            "self_id": 10001, "raw_message": text if text is not None else raw_message,
            "message": segments}


def group_image_event(segments, at_bot=False, user_id=20002, group_id=30003):
    full = list(segments)
    if at_bot:
        full.insert(0, {"type": "at", "data": {"qq": "10001"}})
    return {"post_type": "message", "message_type": "group", "user_id": user_id,
            "group_id": group_id, "self_id": 10001, "raw_message": "", "message": full}


class FakeService(object):
    """假的 AgentService：只记录调用（含 attachments）并返回固定回复。"""

    def __init__(self, reply="这是回复。"):
        self.reply = reply
        self.calls = []

    def ask(self, text, session_id="default", on_text=None, attachments=None):
        self.calls.append({"text": text, "session_id": session_id,
                           "attachments": attachments})
        if on_text:
            on_text(self.reply)
        return self.reply


class QQAdapterTest(unittest.TestCase):

    def setUp(self):
        self.onebot = MockOneBotServer().start()
        self.adapter = QQBotAdapter(make_config(self.onebot.api_base))

    def tearDown(self):
        self.onebot.stop()

    def test_ignore_non_message_event(self):
        self.assertIsNone(self.adapter.parse_event({"post_type": "notice"}))

    def test_ignore_self_message(self):
        self.assertIsNone(self.adapter.parse_event(private_event(user_id=10001)))

    def test_parse_private_message(self):
        message = self.adapter.parse_event(private_event("你好"))
        self.assertEqual(message.channel, "qq")
        self.assertEqual(message.text, "你好")
        self.assertEqual(message.session_id, "qq:private:20002")
        self.assertEqual(message.reply_to, {"type": "private", "id": "20002"})

    def test_group_requires_at(self):
        self.assertIsNone(self.adapter.parse_event(group_event(at_bot=False)))
        message = self.adapter.parse_event(group_event(at_bot=True, text=" 看一下目录"))
        self.assertEqual(message.session_id, "qq:group:30003")
        self.assertEqual(message.text.strip(), "看一下目录")

    def test_allow_users_whitelist(self):
        adapter = QQBotAdapter(make_config(self.onebot.api_base, allow_users="111,222"))
        self.assertIsNone(adapter.parse_event(private_event(user_id="999")))
        self.assertIsNotNone(adapter.parse_event(private_event(user_id="111")))

    def test_handle_sends_reply_to_private(self):
        service = FakeService("你好呀，我是 Agent。")
        reply = self.adapter.handle(private_event("你好"), service)
        self.assertEqual(reply, "你好呀，我是 Agent。")
        self.assertEqual(service.calls[0]["session_id"], "qq:private:20002")
        # 文字消息不带附件，但 attachments 必须显式传下去（空字典）
        self.assertEqual(service.calls[0]["attachments"], {})
        sent = self.onebot.sent_messages()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["action"], "send_private_msg")
        self.assertEqual(sent[0]["payload"]["message"], "你好呀，我是 Agent。")

    def test_handle_sends_group_reply_with_at(self):
        adapter = QQBotAdapter(make_config(self.onebot.api_base, at_sender=True))
        service = FakeService("群回复")
        adapter.handle(group_event("你好"), service)
        self.assertEqual(service.calls[0]["attachments"], {})
        sent = self.onebot.sent_messages()
        self.assertEqual(sent[0]["action"], "send_group_msg")
        self.assertTrue(sent[0]["payload"]["message"].startswith("[CQ:at,qq=20002]"))

    def test_long_reply_is_split(self):
        adapter = QQBotAdapter(make_config(self.onebot.api_base, max_chars=20))
        result = adapter.send_reply(
            self.adapter.parse_event(private_event("你好")), "这是一段很长的回复，" * 5)
        self.assertTrue(result["ok"])
        self.assertGreater(result["parts"], 1)

    def test_health_reports_bot_id(self):
        health = self.adapter.health()
        self.assertTrue(health["ok"], health)
        self.assertEqual(health["bot_id"], "10001")

    def test_health_when_disabled(self):
        adapter = QQBotAdapter(make_config(self.onebot.api_base, enabled=False))
        health = adapter.health()
        self.assertTrue(health["ok"])
        self.assertFalse(health["enabled"])


class QQImageSegmentTest(unittest.TestCase):
    """PHASE 3：QQ image 段 → ChannelMessage.attachments 的解析与透传（全部离线）。"""

    def setUp(self):
        self.onebot = MockOneBotServer().start()
        self.adapter = QQBotAdapter(make_config(self.onebot.api_base))

    def tearDown(self):
        self.onebot.stop()

    # ---- 1. image + url ----

    def test_image_url_is_parsed(self):
        message = self.adapter.parse_event(
            private_image_event([image_segment(url="http://example.com/a.jpg")]))
        self.assertIsNotNone(message)
        self.assertEqual(message.attachments, {"images": ["http://example.com/a.jpg"]})
        self.assertEqual(message.text, DEFAULT_IMAGE_TEXT)   # 纯图片消息补默认问句

    # ---- 2. image + file 兜底 ----

    def test_image_file_is_used_when_url_missing(self):
        message = self.adapter.parse_event(
            private_image_event([image_segment(file="cached_a.jpg")]))
        self.assertEqual(message.attachments, {"images": ["cached_a.jpg"]})

    # ---- 3. url 优先于 file ----

    def test_image_url_wins_over_file(self):
        message = self.adapter.parse_event(
            private_image_event([image_segment(url="http://example.com/a.jpg", file="cached.jpg")]))
        self.assertEqual(message.attachments, {"images": ["http://example.com/a.jpg"]})

    def test_blank_url_falls_back_to_file(self):
        message = self.adapter.parse_event(
            private_image_event([image_segment(url="   ", file="cached.jpg")]))
        self.assertEqual(message.attachments, {"images": ["cached.jpg"]})

    # ---- 4. url/file 都为空 → 跳过；全都为空 → 仍然丢弃 ----

    def test_empty_image_segment_is_skipped(self):
        message = self.adapter.parse_event(private_image_event(
            [image_segment(), {"type": "text", "data": {"text": "看看这个"}}]))
        self.assertEqual(message.text, "看看这个")
        self.assertEqual(message.attachments, {})

    def test_empty_image_segment_alone_is_dropped(self):
        self.assertIsNone(self.adapter.parse_event(private_image_event([image_segment()])))

    def test_image_without_data_field_is_skipped(self):
        message = self.adapter.parse_event(private_image_event(
            [{"type": "image"}, {"type": "text", "data": {"text": "看图"}}]))
        self.assertEqual(message.attachments, {})
        self.assertEqual(message.text, "看图")

    # ---- 5. 文字 + 图片 ----

    def test_text_plus_image_keeps_both(self):
        message = self.adapter.parse_event(private_image_event(
            [{"type": "text", "data": {"text": "这是什么？"}},
             image_segment(url="http://example.com/b.jpg")], text="这是什么？"))
        self.assertEqual(message.text, "这是什么？")                 # 文字原样保留
        self.assertEqual(message.attachments, {"images": ["http://example.com/b.jpg"]})

    # ---- 6. 纯图片 → 默认问句 ----

    def test_image_only_uses_default_question(self):
        message = self.adapter.parse_event(
            private_image_event([image_segment(file="only.jpg")]))
        self.assertEqual(message.text, DEFAULT_IMAGE_TEXT)
        self.assertEqual(message.attachments, {"images": ["only.jpg"]})

    def test_whitespace_text_with_image_uses_default_question(self):
        message = self.adapter.parse_event(private_image_event(
            [{"type": "text", "data": {"text": "   "}}, image_segment(file="only.jpg")]))
        self.assertEqual(message.text, DEFAULT_IMAGE_TEXT)

    # ---- 7. 多图片保持顺序，不截断 ----

    def test_multiple_images_keep_order_without_truncation(self):
        message = self.adapter.parse_event(private_image_event([
            image_segment(url="http://example.com/1.jpg"),
            image_segment(file="2.jpg"),
            image_segment(url="http://example.com/3.jpg"),
        ]))
        self.assertEqual(message.attachments,
                         {"images": ["http://example.com/1.jpg", "2.jpg",
                                     "http://example.com/3.jpg"]})

    # ---- 8. text + at + image 混合段 ----

    def test_mixed_text_at_image_segments(self):
        message = self.adapter.parse_event(group_image_event([
            {"type": "text", "data": {"text": "看看 "}},
            {"type": "at", "data": {"qq": "10001"}},
            {"type": "image", "data": {"url": "http://example.com/m.jpg"}},
        ], at_bot=True))
        self.assertEqual(message.session_id, "qq:group:30003")
        self.assertIn("看看", message.text)
        self.assertNotIn("10001", message.text)                       # @ 不回灌成文字
        self.assertEqual(message.attachments, {"images": ["http://example.com/m.jpg"]})

    def test_at_other_user_is_not_bot_mention(self):
        message = self.adapter.parse_event(group_image_event([
            {"type": "at", "data": {"qq": "99999"}},
            {"type": "image", "data": {"url": "http://example.com/m.jpg"}},
        ], at_bot=False))
        self.assertIsNone(message)                                    # 没 @ 到机器人 → 不处理

    # ---- 9 / 10. 私聊透传 + 群聊 @ 门槛 ----

    def test_private_image_reaches_service_with_attachments(self):
        service = FakeService("图片收到。")
        reply = self.adapter.handle(
            private_image_event([{"type": "text", "data": {"text": "这是什么？"}},
                                 image_segment(url="http://example.com/a.jpg")],
                                text="这是什么？"), service)
        self.assertEqual(reply, "图片收到。")
        call = service.calls[0]
        self.assertEqual(call["text"], "这是什么？")
        self.assertEqual(call["session_id"], "qq:private:20002")
        self.assertEqual(call["attachments"], {"images": ["http://example.com/a.jpg"]})
        self.assertEqual(len(self.onebot.sent_messages()), 1)          # 回复照常发出

    def test_group_image_requires_at(self):
        self.assertIsNone(self.adapter.parse_event(group_image_event(
            [image_segment(url="http://example.com/g.jpg")], at_bot=False)))
        message = self.adapter.parse_event(group_image_event(
            [image_segment(url="http://example.com/g.jpg")], at_bot=True))
        self.assertIsNotNone(message)
        self.assertEqual(message.attachments, {"images": ["http://example.com/g.jpg"]})

    # ---- 11 / 12. 自己发的 / 非 message 事件 ----

    def test_self_image_message_is_ignored(self):
        self.assertIsNone(self.adapter.parse_event(private_image_event(
            [image_segment(url="http://example.com/a.jpg")], user_id=10001)))

    def test_non_message_event_with_image_is_ignored(self):
        payload = private_image_event([image_segment(url="http://example.com/a.jpg")])
        payload["post_type"] = "notice"
        self.assertIsNone(self.adapter.parse_event(payload))

    # ---- 13. 旧文字消息回归 ----

    def test_plain_text_message_has_empty_attachments(self):
        message = self.adapter.parse_event(private_event("你好"))
        self.assertEqual(message.attachments, {})
        self.assertEqual(message.text, "你好")
        self.assertEqual(message.raw["post_type"], "message")

    def test_legacy_string_message_has_empty_attachments(self):
        """message 不是数组的老格式：按老行为取 raw_message，不解析图片。"""
        payload = {"post_type": "message", "message_type": "private", "user_id": 20002,
                   "self_id": 10001, "raw_message": "老格式文本", "message": "老格式文本"}
        message = self.adapter.parse_event(payload)
        self.assertEqual(message.text, "老格式文本")
        self.assertEqual(message.attachments, {})


if __name__ == "__main__":
    unittest.main()
