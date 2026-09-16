# -*- coding: utf-8 -*-
"""PHASE B6：Agent 的 Vision/Audio Specialist 编排测试（全部离线）。

覆盖规范要求的 15 项：纯文字不调用 Specialist、图片只调 Vision、音频只调 STT、
图+文 / 音+文 / 图+音+文顺序、两侧不可用降级、STT 失败不编造、ffmpeg 失败不请求 STT、
临时 WAV 清理（正常与异常）、transcript 与音频内容隔离、纯文字路径不变。
"""

import json
import os
import stat
import tempfile
import unittest

from adapter.model import audio_preprocess as ap
from adapter.model.whisper import WhisperError
from agent import AUDIO_CONTEXT_PREFIX, AUDIO_TRANSCRIPT_MAX_CHARS, Agent
from model.provider import ModelInfo, ModelProvider
from model.router import (ROUTE_MULTIMODAL, ROUTE_TEXT, ROUTE_VISION,
                          LightweightModelRouter)
from tests.test_audio_provider import write_wav
from tests.test_audio_provider import MockWhisperServer
from tests.test_vision_agent import FakeVisionProvider, RecordingLLM


class FakeAudioProvider(ModelProvider):
    """假 STT：记录被转写的文件路径，可配置不可用或抛错。"""

    name = "fake-whisper"
    role = "audio"
    kind = "audio"

    def __init__(self, text="今天北京天气不错", available=True, error=None, events=None):
        self.text = text
        self._available = available
        self.error = error
        self.calls = []
        self.events = events if events is not None else []

    def model_info(self):
        return ModelInfo(role="audio", name="fake-whisper", kind="audio", provider="fake",
                         endpoint="http://127.0.0.1:1", supports_text=False,
                         supports_vision=False, supports_audio=self._available,
                         state="READY" if self._available else "UNAVAILABLE",
                         detail="测试用假 STT")

    def supports_text(self):
        return False

    def supports_vision(self):
        return False

    def supports_audio(self):
        return self._available

    def transcribe(self, audio_path):
        self.events.append("audio")
        self.calls.append(audio_path)
        if self.error:
            raise self.error
        return self.text

    def chat(self, messages, **kwargs):
        raise WhisperError("FakeAudioProvider 不支持 chat()")

    def stream(self, messages, **kwargs):
        raise WhisperError("FakeAudioProvider 不支持 stream()")

    def health(self):
        return {"ok": self._available}


class FakeRegistry(object):
    """给 Router 用的最小 registry：同时提供 vision 与 audio。"""

    def __init__(self, vision=None, audio=None):
        self._providers = {"vision": vision, "audio": audio}

    def get(self, role):
        return self._providers.get(role)


class RecordingVisionProvider(FakeVisionProvider):
    """在假视觉模型基础上，额外把调用顺序记录到 events（用于验证 Vision → Audio）。"""

    def __init__(self, text="一只猫", available=True, events=None):
        FakeVisionProvider.__init__(self, text=text, available=available)
        self.events = events if events is not None else []

    def chat(self, messages, **kwargs):
        self.events.append("vision")
        return FakeVisionProvider.chat(self, messages, **kwargs)


def make_agent(tmpdir, replies=None, vision=None, audio=None, events=None, **overrides):
    """造一个离线 Agent：假主模型 + 假 Vision + 假 STT + 确定性 Router。"""
    events = events if events is not None else []
    llm = RecordingLLM(replies)
    vision = vision if vision is not None else RecordingVisionProvider(events=events)
    audio = audio if audio is not None else FakeAudioProvider(events=events)
    router = LightweightModelRouter(registry=FakeRegistry(vision=vision, audio=audio))
    params = dict(
        verbose=False, stream=True, max_context=2048, server_context=2048,
        memory_path=os.path.join(tmpdir, "memory.json"), memory_enabled=True,
        memory_max_items=5, tool_result_max_chars=4000, memory_max_ratio=0.10,
        system_prompt_max_tokens=300, model_router=router,
        vision_provider=vision, audio_provider=audio,
        audio_ffmpeg="/nonexistent/ffmpeg",      # 默认禁止调用真实 ffmpeg
    )
    params.update(overrides)
    return Agent(llm=llm, **params), llm, vision, audio


class AudioAgentTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ma-audio-agent-")
        self._orig_tempdir = ap.tempfile.gettempdir
        ap.tempfile.gettempdir = lambda: self.tmpdir
        self.wav = write_wav(os.path.join(self.tmpdir, "voice.wav"))

    def tearDown(self):
        ap.tempfile.gettempdir = self._orig_tempdir

    def fake_ffmpeg(self, mode="ok"):
        path = os.path.join(self.tmpdir, "ffmpeg-fake-%s" % mode)
        script = ('#!/usr/bin/env python3\n'
                  'import sys, wave\n'
                  'if "%s" != "ok":\n'
                  '    sys.stderr.write("boom\\n"); sys.exit(1)\n'
                  'out = sys.argv[-1]\n'
                  'w = wave.open(out, "wb"); w.setnchannels(1); w.setsampwidth(2)\n'
                  'w.setframerate(16000); w.writeframes(b"\\x01\\x00" * 8000); w.close()\n'
                  % mode)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(script)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    def temp_leftovers(self):
        return [n for n in os.listdir(self.tmpdir) if n.startswith("mobile-agent-audio-")]

    # ---- 1. 纯文字 ----

    def test_text_only_calls_no_specialist(self):
        agent, llm, vision, audio = make_agent(self.tmpdir)
        answer = agent.ask("你好")
        self.assertTrue(answer)
        self.assertEqual(llm.calls, 1)
        self.assertEqual(len(vision.payloads), 0)
        self.assertEqual(len(audio.calls), 0)
        self.assertEqual(agent.last_route["route"], ROUTE_TEXT)
        self.assertEqual(agent.last_timing["vision_calls"], 0)
        self.assertEqual(agent.last_timing["audio_calls"], 0)

    # ---- 2. 只有图片 ----

    def test_image_only_calls_vision_once(self):
        agent, llm, vision, audio = make_agent(self.tmpdir)
        agent.ask("这是什么？", attachments={"images": ["a.jpg"]})
        self.assertEqual(len(vision.payloads), 1)
        self.assertEqual(len(audio.calls), 0)
        self.assertEqual(llm.calls, 1)
        self.assertIn(agent.last_route["route"], (ROUTE_VISION, "VISION_TO_TEXT"))
        self.assertEqual(agent.last_timing["audio_calls"], 0)

    # ---- 3. 只有音频 ----

    def test_audio_only_calls_stt_once_and_injects_transcript(self):
        audio = FakeAudioProvider(text="帮我查一下明天的天气")
        agent, llm, vision, _audio = make_agent(self.tmpdir, audio=audio)
        agent.ask("（语音）", attachments={"audios": [self.wav]})
        self.assertEqual(len(audio.calls), 1)
        self.assertEqual(len(vision.payloads), 0)
        self.assertEqual(llm.calls, 1)
        prompt = json.dumps(llm.messages[0], ensure_ascii=False)
        self.assertIn(AUDIO_CONTEXT_PREFIX, prompt)
        self.assertIn("帮我查一下明天的天气", prompt)
        self.assertEqual(agent.last_timing["audio_state"], "READY")

    # ---- 4. 图片 + 文字 ----

    def test_image_with_text_has_no_transcript(self):
        agent, llm, vision, audio = make_agent(self.tmpdir)
        agent.ask("这张图里有什么？", attachments={"images": ["a.jpg"]})
        self.assertEqual(len(vision.payloads), 1)
        self.assertEqual(len(audio.calls), 0)
        self.assertNotIn(AUDIO_CONTEXT_PREFIX,
                         json.dumps(llm.messages[0], ensure_ascii=False))

    # ---- 5. 音频 + 文字 ----

    def test_audio_with_text_calls_stt_once(self):
        audio = FakeAudioProvider(text="记得买牛奶")
        agent, llm, vision, _audio = make_agent(self.tmpdir, audio=audio)
        # 注意：不能用「记一下」这类词，那会命中记忆意图（零模型调用）提前返回
        agent.ask("这段语音说了什么？", attachments={"audios": [self.wav]})
        self.assertEqual(len(audio.calls), 1)
        self.assertEqual(len(vision.payloads), 0)
        self.assertEqual(llm.calls, 1)

    # ---- 6. 图片 + 音频 + 文字：各一次，顺序 Vision → Audio ----

    def test_image_plus_audio_runs_both_once_in_order(self):
        events = []
        vision = RecordingVisionProvider(text="一只猫", events=events)
        audio = FakeAudioProvider(text="这是猫吗", events=events)
        agent, llm, _v, _a = make_agent(self.tmpdir, vision=vision, audio=audio, events=events)
        agent.ask("看看这张图，再听一下我说的话",
                  attachments={"images": ["a.jpg"], "audios": [self.wav]})
        self.assertEqual(events, ["vision", "audio"])
        self.assertEqual(len(vision.payloads), 1)
        self.assertEqual(len(audio.calls), 1)
        self.assertEqual(llm.calls, 1)
        prompt = json.dumps(llm.messages[0], ensure_ascii=False)
        self.assertIn("一只猫", prompt)
        self.assertIn("这是猫吗", prompt)
        self.assertIn(agent.last_route["route"], (ROUTE_MULTIMODAL, "VISION_TO_TEXT"))

    # ---- 7. Vision 不可用 ----

    def test_vision_unavailable_is_stated_not_faked(self):
        vision = FakeVisionProvider(available=False)
        agent, llm, _v, _a = make_agent(self.tmpdir, vision=vision)
        answer = agent.ask("这是什么？", attachments={"images": ["a.jpg"]})
        self.assertTrue(answer)
        self.assertEqual(len(vision.payloads), 0)
        prompt = json.dumps(llm.messages[0], ensure_ascii=False)
        self.assertIn("视觉模型不可用", prompt)          # Router 的降级说明
        self.assertNotIn("图片理解结果", prompt)         # 没有伪造视觉结果
        self.assertEqual(agent.last_timing["vision_calls"], 0)

    # ---- 8. Audio 不可用 ----

    def test_audio_unavailable_is_stated_not_faked(self):
        audio = FakeAudioProvider(available=False)
        agent, llm, _v, _a = make_agent(self.tmpdir, audio=audio)
        answer = agent.ask("（语音）", attachments={"audios": [self.wav]})
        self.assertTrue(answer)
        self.assertEqual(len(audio.calls), 0)          # 不存在的服务绝不调用
        prompt = json.dumps(llm.messages[0], ensure_ascii=False)
        self.assertIn("音频", prompt)
        self.assertNotIn(AUDIO_CONTEXT_PREFIX, prompt)
        self.assertEqual(agent.last_timing["audio_calls"], 0)
        self.assertEqual(agent.last_timing["audio_state"], "UNAVAILABLE")

    # ---- 9. STT 失败不编造 ----

    def test_stt_failure_does_not_invent_transcript(self):
        audio = FakeAudioProvider(error=WhisperError("STT 服务返回 500：boom"))
        agent, llm, _v, _a = make_agent(self.tmpdir, audio=audio)
        answer = agent.ask("（语音）", attachments={"audios": [self.wav]})
        self.assertTrue(answer)
        self.assertEqual(len(audio.calls), 1)          # 试过一次
        prompt = json.dumps(llm.messages[0], ensure_ascii=False)
        self.assertNotIn(AUDIO_CONTEXT_PREFIX, prompt)  # 没有编造转写
        self.assertIn("Audio transcription unavailable", prompt)
        self.assertEqual(agent.last_timing["audio_state"], "ERROR")

    # ---- 10. ffmpeg 失败 → 不调用 STT ----

    def test_ffmpeg_failure_skips_stt(self):
        broken = os.path.join(self.tmpdir, "voice.amr")
        with open(broken, "wb") as handle:
            handle.write(b"not audio")
        audio = FakeAudioProvider()
        agent, llm, _v, _a = make_agent(self.tmpdir, audio=audio)
        agent.ask("（语音）", attachments={"audios": [broken]})
        self.assertEqual(len(audio.calls), 0)          # 关键：转码失败就不请求 STT
        self.assertEqual(agent.last_timing["audio_state"], "ERROR")
        self.assertIn("Audio transcription unavailable",
                      json.dumps(llm.messages[0], ensure_ascii=False))
        self.assertEqual(self.temp_leftovers(), [])

    # ---- 11 / 12. 临时文件清理（正常 + 异常） ----

    def test_temp_wav_is_deleted_after_request(self):
        source = os.path.join(self.tmpdir, "voice.mp3")
        with open(source, "wb") as handle:
            handle.write(b"ID3 fake mp3")
        audio = FakeAudioProvider()
        agent, _llm, _v, _a = make_agent(
            self.tmpdir, audio=audio, audio_ffmpeg=self.fake_ffmpeg())
        agent.ask("（语音）", attachments={"audios": [source]})
        self.assertEqual(len(audio.calls), 1)
        temp_wav = audio.calls[0]
        self.assertNotEqual(temp_wav, source)          # 用的是转码后的临时文件
        self.assertFalse(os.path.exists(temp_wav))     # 请求结束后已删除
        self.assertEqual(self.temp_leftovers(), [])
        self.assertTrue(os.path.isfile(source))        # 原始文件不动

    def test_temp_wav_is_deleted_even_when_stt_fails(self):
        source = os.path.join(self.tmpdir, "voice.mp3")
        with open(source, "wb") as handle:
            handle.write(b"ID3 fake mp3")
        audio = FakeAudioProvider(error=WhisperError("boom"))
        agent, _llm, _v, _a = make_agent(
            self.tmpdir, audio=audio, audio_ffmpeg=self.fake_ffmpeg())
        agent.ask("（语音）", attachments={"audios": [source]})
        self.assertEqual(len(audio.calls), 1)
        self.assertFalse(os.path.exists(audio.calls[0]))
        self.assertEqual(self.temp_leftovers(), [])

    # ---- 13 / 14. 隔离性 ----

    def test_transcript_not_written_to_memory(self):
        audio = FakeAudioProvider(text="我的银行卡密码是1234")
        agent, _llm, _v, _a = make_agent(self.tmpdir, audio=audio)
        agent.ask("（语音）", attachments={"audios": [self.wav]})
        with open(agent.memory_path, "r", encoding="utf-8") as handle:
            raw = handle.read()
        self.assertNotIn("我的银行卡密码是1234", raw)
        self.assertNotIn("voice.wav", raw)
        self.assertNotIn("mobile-agent-audio-", raw)

    def test_audio_content_not_written_to_history(self):
        audio = FakeAudioProvider(text="转写内容不应进入历史")
        agent, _llm, _v, _a = make_agent(self.tmpdir, audio=audio)
        agent.ask("（语音）", attachments={"audios": [self.wav]})
        blob = json.dumps(agent.history, ensure_ascii=False)
        for message in agent.history:
            self.assertIsInstance(message["content"], str)
        self.assertNotIn("转写内容不应进入历史", blob)   # transcript 只在本轮 prompt 里
        self.assertNotIn("voice.wav", blob)
        self.assertNotIn("base64", blob)

    # ---- 15. 纯文字路径与原路径一致 ----

    def test_text_path_is_unchanged(self):
        agent, llm, vision, audio = make_agent(self.tmpdir)
        seen = []
        answer = agent.ask("你好", on_text=seen.append)
        self.assertEqual(answer, "这是主模型的回答。")
        self.assertEqual(llm.calls, 1)
        self.assertEqual(len(vision.payloads), 0)
        self.assertEqual(len(audio.calls), 0)
        self.assertTrue(seen)
        self.assertEqual(agent.last_timing["vision_ms"], 0)
        self.assertEqual(agent.last_timing["audio_ms"], 0)

    def test_transcript_is_truncated(self):
        long_text = "很长的转写" * 500
        audio = FakeAudioProvider(text=long_text)
        agent, llm, _v, _a = make_agent(self.tmpdir, audio=audio)
        agent.ask("（语音）", attachments={"audios": [self.wav]})
        prompt = json.dumps(llm.messages[0], ensure_ascii=False)
        self.assertIn("已截断", prompt)                     # 超长被截断并标记
        self.assertEqual(llm.calls, 1)
        # 注入到 Primary 的转写长度受上限约束（+ 截断标记 + 少量包裹字符）
        self.assertNotIn(long_text, prompt)
        self.assertLess(len(prompt), len(long_text))
        self.assertGreater(AUDIO_TRANSCRIPT_MAX_CHARS, 0)
        self.assertLessEqual(AUDIO_TRANSCRIPT_MAX_CHARS, 2000)


class AudioServiceIntegrationTest(unittest.TestCase):
    """Mock 全链路：AgentService → Session → Agent → WhisperProvider → Primary。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ma-audio-svc-")
        self.wav = write_wav(os.path.join(self.tmpdir, "voice.wav"))

    def test_service_routes_audio_attachment_through_whisper(self):
        from tests.helpers import make_config
        from tests.mock_model import MockModelServer

        # 注意：主模型回复里刻意不含转写文本，避免把「模型自己的回答」误判成历史泄漏
        primary = MockModelServer(reply="好的，已经帮你总结完了。").start()
        whisper = MockWhisperServer(body='{"text":"请帮我总结一下"}').start()
        try:
            config = make_config(primary.base_url, self.tmpdir)
            config["models"] = {
                "vision": {"enabled": False},
                "audio": {"enabled": True, "provider": "whisper", "name": "whisper-base",
                          "base_url": whisper.base_url, "language": "zh", "timeout": 30,
                          "prompt": "以下是简体中文普通话的转写。"},
            }
            from runtime.service import AgentService
            service = AgentService(config=config, auto_probe=True)
            # 能力探测：whisper /health 200 → audio 可用
            self.assertTrue(service.model_registry.audio().supports_audio())

            reply = service.ask("（语音）", session_id="s1", attachments={"audios": [self.wav]})
            self.assertEqual(reply, "好的，已经帮你总结完了。")
            # 真的发出了 STT 请求，并且带上了 initial prompt
            self.assertEqual(len(whisper.requests), 1)
            body = whisper.requests[0]["body"]
            self.assertIn(b'name="prompt"', body)
            self.assertIn("以下是简体中文普通话的转写。".encode("utf-8"), body)
            # 转写结果进入了本轮 prompt，但没有进入 history / memory
            blob = json.dumps(primary.calls[-1]["messages"], ensure_ascii=False)
            self.assertIn("语音转写结果：请帮我总结一下", blob)
            agent = service.sessions.get("s1").agent
            self.assertTrue(all(isinstance(m.get("content"), str) for m in agent.history))
            self.assertNotIn("请帮我总结一下",
                             json.dumps(agent.history, ensure_ascii=False))
            with open(agent.memory_path, "r", encoding="utf-8") as handle:
                self.assertNotIn("请帮我总结一下", handle.read())
            self.assertEqual(agent.last_timing["audio_calls"], 1)

            # 纯文字请求：不得触发 STT
            service.ask("你好", session_id="s1")
            self.assertEqual(len(whisper.requests), 1)
        finally:
            primary.stop()
            whisper.stop()


if __name__ == "__main__":
    unittest.main()
