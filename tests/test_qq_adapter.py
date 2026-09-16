# -*- coding: utf-8 -*-
"""QQ 适配器测试：事件解析、白名单、分片发送、完整 handle 流程。"""

import unittest

from adapter.qq import QQBotAdapter
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


class FakeService(object):
    """假的 AgentService：只记录调用并返回固定回复。"""

    def __init__(self, reply="这是回复。"):
        self.reply = reply
        self.calls = []

    def ask(self, text, session_id="default", on_text=None):
        self.calls.append({"text": text, "session_id": session_id})
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
        sent = self.onebot.sent_messages()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["action"], "send_private_msg")
        self.assertEqual(sent[0]["payload"]["message"], "你好呀，我是 Agent。")

    def test_handle_sends_group_reply_with_at(self):
        adapter = QQBotAdapter(make_config(self.onebot.api_base, at_sender=True))
        adapter.handle(group_event("你好"), FakeService("群回复"))
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


if __name__ == "__main__":
    unittest.main()
