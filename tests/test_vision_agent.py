# -*- coding: utf-8 -*-
"""PHASE 2 测试：Vision 最小完整链路（全部离线，用假 Provider，不联网、不依赖手机）。

覆盖点：
  - 纯文字不经过视觉模型（零额外调用）；
  - 图片附件走 Router -> Vision -> Primary；
  - 视觉结果注入当前轮，且被限制在 400 字以内；
  - 图片 / 多模态 payload 永不写入 history / memory；
  - 视觉不可用或调用失败时明确降级，绝不伪造「看到了图片」；
  - 一次图片请求只调用一次视觉模型，image_analyze 不重复推理；
  - 旧的两参数调用方式（无 attachments）保持兼容。
"""

import json
import os
import tempfile
import unittest

from agent import VISION_RESULT_MAX_CHARS, VISUAL_CONTEXT_PREFIX, Agent
from gateway.base import ChannelMessage
from llm import LLMError
from model.provider import ModelInfo, ModelProvider, STATE_READY
from model.router import (ROUTE_TEXT, ROUTE_VISION, ROUTE_VISION_TO_TEXT,
                          LightweightModelRouter)
from runtime.session import Session, SessionManager
from tests import helpers
from tests.mock_model import MockModelServer

TRUNCATED_MARK = "…（已截断）"


class RecordingLLM(object):
    """假主模型：记录收到的全部 messages，按脚本返回文本。"""

    def __init__(self, replies=None):
        self.replies = list(replies or ["这是主模型的回答。"])
        self.messages = []
        self.calls = 0
        self.last_timings = {}
        self.last_usage = {}

    def chat(self, messages, **kwargs):
        self.calls += 1
        self.messages.append(messages)
        return self.replies.pop(0) if self.replies else "这是主模型的回答。"

    def chat_stream(self, messages, **kwargs):
        yield self.chat(messages)


class FakeVisionProvider(ModelProvider):
    """假视觉专家模型：可配置可用性、回复与错误，并记录收到的多模态 payload。"""

    name = "fake-vision"
    role = "vision"
    kind = "vision"

    def __init__(self, text="一只橘猫趴在窗台上。", available=True, error=None):
        self.text = text
        self._available = available
        self.error = error
        self.payloads = []

    def model_info(self):
        return ModelInfo(role="vision", name="fake-vision", kind="vision",
                         provider="fake", endpoint="http://127.0.0.1:1/v1",
                         supports_text=True, supports_vision=self._available,
                         state=STATE_READY if self._available else "UNAVAILABLE",
                         detail="测试用假视觉模型")

    def supports_vision(self):
        return self._available

    def chat(self, messages, **kwargs):
        self.payloads.append(messages)
        if self.error:
            raise self.error
        return self.text

    def stream(self, messages, **kwargs):
        yield self.chat(messages)

    def health(self):
        return {"ok": self._available}

    @property
    def calls(self) -> int:
        """被真实调用过的次数（不可用时必须为 0）。"""
        return len(self.payloads)


class FakeRegistry(object):
    """最小 registry：只回答 vision 角色（Router / Agent 都通过它取 Provider）。"""

    def __init__(self, provider):
        self._provider = provider

    def get(self, role):
        return self._provider if role == "vision" else None


def make_agent(tmpdir, replies=None, vision=None, inject_provider=True, **overrides):
    """造一个离线 Agent：假主模型 + 假视觉模型 + 确定性 Router。"""
    llm = RecordingLLM(replies)
    vision = vision if vision is not None else FakeVisionProvider()
    router = LightweightModelRouter(registry=FakeRegistry(vision))
    params = dict(
        verbose=False, stream=True, max_context=2048, server_context=2048,
        memory_path=os.path.join(tmpdir, "memory.json"), memory_enabled=True,
        memory_max_items=5, tool_result_max_chars=4000, memory_max_ratio=0.10,
        system_prompt_max_tokens=300, model_router=router,
        # inject_provider=False 时模拟「没显式注入」：只能从 registry 取
        vision_provider=vision if inject_provider else None,
    )
    params.update(overrides)
    return Agent(llm=llm, **params), llm, vision


class VisionAgentTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ma-vision-test-")

    # ---- 1. 纯文字不碰视觉模型 ----

    def test_text_request_never_touches_vision(self):
        agent, llm, vision = make_agent(self.tmpdir)
        answer = agent.ask("你好")
        self.assertEqual(answer, "这是主模型的回答。")
        self.assertEqual(llm.calls, 1)
        self.assertEqual(vision.calls, 0)
        self.assertEqual(agent.last_route["route"], ROUTE_TEXT)
        self.assertEqual(agent.last_timing["vision_calls"], 0)
        self.assertEqual(agent.last_timing["vision_ms"], 0)

    # ---- 2. 图片附件走 Vision ----

    def test_image_attachment_routes_to_vision(self):
        agent, _llm, vision = make_agent(self.tmpdir)
        agent.ask("这是什么？", attachments={"images": ["http://example.com/a.jpg"]})
        self.assertIn(agent.last_route["route"], (ROUTE_VISION, ROUTE_VISION_TO_TEXT))
        self.assertEqual(agent.last_route["steps"][0], "vision")
        self.assertEqual(agent.last_timing["vision_state"], "READY")
        self.assertEqual(agent.last_timing["vision_calls"], 1)
        self.assertEqual(vision.calls, 1)
        # 多模态 payload 结构正确：文本 + image_url
        content = vision.payloads[0][0]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")
        self.assertEqual(content[1]["image_url"]["url"], "http://example.com/a.jpg")
        self.assertIn("这是什么？", content[0]["text"])

    # ---- 3. 视觉结果注入 Primary ----

    def test_vision_result_is_injected_into_primary_prompt(self):
        vision = FakeVisionProvider(text="图里是一只橘猫，趴在窗台上。")
        agent, llm, _vision = make_agent(self.tmpdir, vision=vision)
        agent.ask("这是什么？", attachments={"images": ["a.jpg"]})
        prompt = llm.messages[0]
        last_user = [m for m in prompt if m["role"] == "user"][-1]
        self.assertIn(VISUAL_CONTEXT_PREFIX, last_user["content"])
        self.assertIn("橘猫", last_user["content"])
        # 普通文字链路不受影响：主模型只被调用一次
        self.assertEqual(llm.calls, 1)

    # ---- 4. 视觉结果截断到 400 字 ----

    def test_vision_result_truncated_to_400_chars(self):
        vision = FakeVisionProvider(text="猫" * 1000)
        agent, llm, _vision = make_agent(self.tmpdir, vision=vision)
        visual, status = agent._vision_stage("这是什么", ["a.jpg"])
        self.assertTrue(visual.startswith(VISUAL_CONTEXT_PREFIX))
        body = visual[len(VISUAL_CONTEXT_PREFIX):]
        self.assertEqual(len(body), VISION_RESULT_MAX_CHARS + len(TRUNCATED_MARK))
        self.assertTrue(body.endswith(TRUNCATED_MARK))
        self.assertEqual(status["state"], "READY")
        self.assertEqual(status["calls"], 1)

        # 注入到主模型上下文里的同样是截断后的版本
        agent.ask("这是什么？", attachments={"images": ["a.jpg"]})
        last_user = [m for m in llm.messages[0] if m["role"] == "user"][-1]
        self.assertIn(TRUNCATED_MARK, last_user["content"])

    # ---- 5 / 6. 图片绝不进 history / memory ----

    def test_image_never_written_to_history_or_memory(self):
        agent, _llm, _vision = make_agent(self.tmpdir)
        agent.ask("这是什么？", attachments={"images": ["http://example.com/secret.jpg"]})
        for message in agent.history:
            self.assertIsInstance(message["content"], str)
            self.assertNotIn("secret.jpg", message["content"])
        with open(agent.memory_path, "r", encoding="utf-8") as handle:
            raw = handle.read()
        self.assertNotIn("secret.jpg", raw)
        self.assertNotIn("image_url", raw)

    def test_multimodal_payload_never_written_to_history(self):
        agent, llm, _vision = make_agent(self.tmpdir)
        agent.ask("这是什么？", attachments={"images": ["a.jpg"]})
        # history 必须是纯文本消息列表
        for message in agent.history:
            if isinstance(message.get("content"), list):
                self.fail("history 里出现了多模态 content 数组")
        self.assertNotIn("image_url", json.dumps(agent.history, ensure_ascii=False))
        # 发给主模型的 prompt 同样只有文本
        self.assertNotIn("image_url", json.dumps(llm.messages[0], ensure_ascii=False))
        self.assertEqual(agent.last_timing["vision_state"], "READY")

    # ---- 7. 视觉不可用 -> 明确降级，绝不假装 ----

    def test_vision_unavailable_degrades_without_faking(self):
        vision = FakeVisionProvider(available=False)
        agent, llm, _vision = make_agent(self.tmpdir, vision=vision)
        answer = agent.ask("这是什么？", attachments={"images": ["a.jpg"]})
        self.assertTrue(answer)
        self.assertEqual(vision.calls, 0)                      # 一次都没调用
        self.assertEqual(agent.last_timing["vision_calls"], 0)
        self.assertEqual(agent.last_timing["vision_state"], "UNAVAILABLE")
        prompt = json.dumps(llm.messages[0], ensure_ascii=False)
        self.assertIn("视觉模型不可用", prompt)                 # Router 的降级说明
        self.assertNotIn(VISUAL_CONTEXT_PREFIX, prompt)        # 没有伪造视觉结果

    def test_vision_resolved_from_registry_when_not_injected(self):
        """未显式注入 Provider 时，从 router 的 registry 取；仍然不可用就不调用。"""
        vision = FakeVisionProvider(available=False)
        agent, _llm, _vision = make_agent(self.tmpdir, vision=vision, inject_provider=False)
        self.assertIs(agent._resolve_vision_provider(), vision)
        agent.ask("这是什么？", attachments={"images": ["a.jpg"]})
        self.assertEqual(vision.calls, 0)
        self.assertEqual(agent.last_timing["vision_state"], "UNAVAILABLE")

    def test_vision_call_failure_degrades_and_still_answers(self):
        vision = FakeVisionProvider(error=LLMError("视觉模型连接失败"))
        agent, llm, _vision = make_agent(self.tmpdir, vision=vision)
        answer = agent.ask("这是什么？", attachments={"images": ["a.jpg"]})
        self.assertTrue(answer)                                # 主模型仍然给出回答
        self.assertEqual(vision.calls, 1)                      # 只尝试了一次
        self.assertEqual(agent.last_timing["vision_state"], "ERROR")
        prompt = json.dumps(llm.messages[0], ensure_ascii=False)
        self.assertIn("Vision model unavailable", prompt)
        self.assertNotIn(VISUAL_CONTEXT_PREFIX, prompt)

    def test_vision_empty_result_is_treated_as_failure(self):
        vision = FakeVisionProvider(text="   ")
        agent, llm, _vision = make_agent(self.tmpdir, vision=vision)
        agent.ask("这是什么？", attachments={"images": ["a.jpg"]})
        self.assertEqual(agent.last_timing["vision_state"], "EMPTY")
        prompt = json.dumps(llm.messages[0], ensure_ascii=False)
        self.assertNotIn(VISUAL_CONTEXT_PREFIX, prompt)
        self.assertIn("Vision model unavailable", prompt)

    # ---- 8. 不重复推理 ----

    def test_no_duplicate_image_analysis(self):
        agent, llm, vision = make_agent(self.tmpdir)
        agent.ask("这是什么？", attachments={"images": ["a.jpg"]})
        self.assertEqual(vision.calls, 1)   # 一次图片请求 = 一次视觉推理
        self.assertEqual(llm.calls, 1)      # 且不多一次主模型往返
        # image_analyze 工具不得再触发一次视觉推理（默认路径已由 Vision stage 承担）
        from tools.vision import image as image_tool
        result = image_tool.image_analyze("a.jpg", "这是什么")
        self.assertFalse(result["ok"])
        self.assertEqual(vision.calls, 1)

    # ---- 9. 多图行为（v0.27 只处理第一张） ----

    def test_multiple_images_uses_first_only(self):
        vision = FakeVisionProvider(text="第一张图")
        agent, _llm, _vision = make_agent(self.tmpdir, vision=vision)
        agent.ask("看看这些图", attachments={"images": ["one.jpg", "two.jpg"]})
        self.assertEqual(vision.calls, 1)
        self.assertEqual(vision.payloads[0][0]["content"][1]["image_url"]["url"], "one.jpg")

    def test_blank_image_entries_are_skipped(self):
        vision = FakeVisionProvider(text="第二张图")
        agent, _llm, _vision = make_agent(self.tmpdir, vision=vision)
        agent.ask("看看这些图", attachments={"images": ["   ", "two.jpg"]})
        self.assertEqual(vision.calls, 1)
        self.assertEqual(vision.payloads[0][0]["content"][1]["image_url"]["url"], "two.jpg")

    def test_all_blank_images_reported_without_call(self):
        vision = FakeVisionProvider()
        agent, _llm, _vision = make_agent(self.tmpdir, vision=vision)
        agent.ask("看看这些图", attachments={"images": ["  "]})
        self.assertEqual(vision.calls, 0)
        self.assertEqual(agent.last_timing["vision_state"], "ERROR")

    # ---- 10. 旧调用方式兼容 ----

    def test_old_text_api_still_compatible(self):
        agent, _llm, vision = make_agent(self.tmpdir)
        seen = []
        answer = agent.ask("你好", on_text=seen.append)   # 不传 attachments
        self.assertEqual(answer, "这是主模型的回答。")
        self.assertEqual(vision.calls, 0)
        self.assertTrue(seen)
        # 第二个位置参数仍然是 on_text，attachments 只能作为关键字传入
        agent.ask("再见", None, {"images": ["a.jpg"]})
        self.assertEqual(vision.calls, 1)


class RecordingAgent(object):
    """会话层用的假 Agent：只记录收到了什么。"""

    def __init__(self):
        self.calls = []

    def ask(self, text, on_text=None, attachments=None):
        self.calls.append({"text": text, "attachments": attachments})
        return "收到：%s" % text


class ChannelAttachmentPlumbingTest(unittest.TestCase):
    """PHASE 2.1 / 2.2 / 2.3：ChannelMessage -> Session -> AgentService 的透传。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ma-plumb-")

    def test_channel_message_attachments_default_and_isolated(self):
        first = ChannelMessage(channel="cli", text="你好")            # 旧构造方式
        second = ChannelMessage(channel="qq", text="看图", attachments={"images": ["a.jpg"]})
        self.assertEqual(first.attachments, {})                      # 默认空字典
        self.assertEqual(first.text, "你好")                          # text 语义未变
        self.assertEqual(second.text, "看图")
        self.assertEqual(second.attachments, {"images": ["a.jpg"]})
        first.attachments["images"] = ["b.jpg"]                       # 默认值不能共享同一个 dict
        self.assertEqual(ChannelMessage().attachments, {})

    def test_channel_message_carries_text_not_multimodal_content(self):
        message = ChannelMessage(channel="qq", text="这是什么？",
                                 attachments={"images": ["a.jpg"]})
        self.assertIsInstance(message.text, str)                       # 多模态内容不进 text
        self.assertIsInstance(message.raw, dict)

    def test_session_passes_attachments_to_agent(self):
        agent = RecordingAgent()
        session = Session("s1", agent)
        session.ask("这是什么？", attachments={"images": ["a.jpg"]})
        session.ask("你好")                                            # 旧调用方式
        self.assertEqual(agent.calls[0]["attachments"], {"images": ["a.jpg"]})
        self.assertIsNone(agent.calls[1]["attachments"])
        self.assertEqual([c["text"] for c in agent.calls], ["这是什么？", "你好"])

    def test_session_manager_passes_attachments_to_agent(self):
        agent = RecordingAgent()
        manager = SessionManager(factory=lambda: agent)
        manager.ask("s1", "这是什么？", attachments={"images": ["a.jpg"]})
        self.assertEqual(agent.calls[0]["attachments"], {"images": ["a.jpg"]})
        self.assertEqual(manager.ask("s1", "你好"), "收到：你好")

    def test_agent_service_passes_attachments_end_to_end(self):
        """AgentService -> Session -> Agent -> Router -> Vision：真链路（两个 mock 服务）。"""
        primary = MockModelServer(reply="这是主模型的回答。").start()
        vision = MockModelServer(reply="图里是一只橘猫。",
                                 modalities={"vision": True, "audio": False, "video": False}).start()
        try:
            config = helpers.make_config(primary.base_url, self.tmpdir)
            config["models"] = {
                "vision": {"enabled": True, "name": "mock-vision", "endpoint": vision.base_url,
                           "timeout": 30, "max_output_tokens": 256},
                "audio": {"enabled": False},
            }
            from runtime.service import AgentService
            service = AgentService(config=config, auto_probe=True)

            before = len(vision.calls)
            self.assertEqual(service.ask("你好", session_id="s1"), "这是主模型的回答。")
            self.assertEqual(len(vision.calls) - before, 0)             # 纯文字不碰视觉模型

            before = len(vision.calls)
            service.ask("这是什么？", session_id="s1", attachments={"images": ["a.jpg"]})
            self.assertEqual(len(vision.calls) - before, 1)             # 图片 = 一次视觉调用
            agent = service.sessions.get("s1").agent
            self.assertEqual(agent.last_timing["vision_state"], "READY")
            # 视觉结果进了本轮 prompt，但图片本身没进 history
            blob = str(primary.calls[-1]["messages"])
            self.assertIn(VISUAL_CONTEXT_PREFIX, blob)
            self.assertNotIn("image_url", blob)
            self.assertTrue(all(isinstance(m.get("content"), str) for m in agent.history))
        finally:
            primary.stop()
            vision.stop()


if __name__ == "__main__":
    unittest.main()
