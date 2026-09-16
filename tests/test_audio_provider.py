# -*- coding: utf-8 -*-
"""PHASE B6：WhisperProvider（STT，标准库 multipart）与音频预处理层测试。

全部离线：用本地假 whisper-server（127.0.0.1 随机端口）+ 假 ffmpeg 脚本，
不依赖真机、不依赖 whisper.cpp、不下载任何东西。
"""

import json
import os
import stat
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from adapter.model import audio_preprocess as ap
from adapter.model.whisper import (WhisperError, WhisperProvider, build_multipart,
                                   parse_transcript)


def write_wav(path, seconds=0.5, rate=16000, channels=1, width=2):
    """写一个真实的 16k/单声道/16bit PCM WAV（用于「已经是目标格式」的用例）。"""
    frames = int(rate * seconds)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(b"\x01\x00" * frames * channels)
    return path


class _WhisperHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        server = self.server
        if self.path.startswith("/health"):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
        elif server.raw_body is not None:          # 充当「远程音频文件」的下载源
            payload = server.raw_body
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_POST(self):
        server = self.server
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        server.requests.append({"path": self.path, "body": body,
                                "content_type": self.headers.get("Content-Type", "")})
        if server.fail_status:
            payload = json.dumps({"error": "boom"}).encode()
            self.send_response(server.fail_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        payload = server.body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class MockWhisperServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, body='{"text":"現在幾點了"}', fail_status=0, port=0, raw_body=None):
        ThreadingHTTPServer.__init__(self, ("127.0.0.1", port), _WhisperHandler)
        self.body = body
        self.fail_status = fail_status
        self.raw_body = raw_body
        self.requests = []
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        try:
            self.shutdown()
            self.server_close()
        except Exception:
            pass

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:%d" % self.server_address[1]


class WhisperProviderTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ma-stt-")
        self.wav = write_wav(os.path.join(self.tmpdir, "voice.wav"))

    def test_transcribe_posts_multipart_and_returns_text(self):
        server = MockWhisperServer().start()
        try:
            provider = WhisperProvider(base_url=server.base_url, language="zh")
            text = provider.transcribe(self.wav)
            self.assertEqual(text, "現在幾點了")
            self.assertEqual(len(server.requests), 1)
            request = server.requests[0]
            self.assertTrue(request["path"].endswith("/inference"))
            self.assertTrue(request["content_type"].startswith("multipart/form-data; boundary="))
            body = request["body"]
            self.assertIn(b'name="file"; filename="voice.wav"', body)
            self.assertIn(b"Content-Type: audio/wav", body)
            self.assertIn(b'name="response_format"', body)
            self.assertIn(b"json", body)
            self.assertIn(b'name="language"', body)
            self.assertIn(b"zh", body)
            # 音频字节被原样传出（取文件内容做包含判断）
            with open(self.wav, "rb") as handle:
                self.assertIn(handle.read(), body)
        finally:
            server.stop()

    def test_initial_prompt_is_sent_only_when_configured(self):
        server = MockWhisperServer().start()
        try:
            plain = WhisperProvider(base_url=server.base_url)
            plain.transcribe(self.wav)
            self.assertNotIn(b'name="prompt"', server.requests[-1]["body"])

            biased = WhisperProvider(base_url=server.base_url,
                                     initial_prompt="以下是简体中文普通话的转写。")
            biased.transcribe(self.wav)
            body = server.requests[-1]["body"]
            self.assertIn(b'name="prompt"', body)
            self.assertIn("以下是简体中文普通话的转写。".encode("utf-8"), body)
            self.assertIn(b'name="carry_initial_prompt"', body)
            self.assertIn(b"true", body)
        finally:
            server.stop()

    def test_http_error_becomes_whisper_error(self):
        server = MockWhisperServer(fail_status=500).start()
        try:
            provider = WhisperProvider(base_url=server.base_url, timeout=5)
            with self.assertRaises(WhisperError) as ctx:
                provider.transcribe(self.wav)
            self.assertIn("500", str(ctx.exception))
        finally:
            server.stop()

    def test_connection_error_is_reported_without_crash(self):
        provider = WhisperProvider(base_url="http://127.0.0.1:1", timeout=3)
        with self.assertRaises(WhisperError) as ctx:
            provider.transcribe(self.wav)
        self.assertIn("无法连接", str(ctx.exception))

    def test_missing_file_raises_before_any_request(self):
        provider = WhisperProvider(base_url="http://127.0.0.1:1", timeout=3)
        with self.assertRaises(WhisperError):
            provider.transcribe(os.path.join(self.tmpdir, "nope.wav"))

    def test_non_json_and_missing_text_are_errors(self):
        with self.assertRaises(WhisperError):
            parse_transcript("<html>oops</html>")
        with self.assertRaises(WhisperError):
            parse_transcript('{"ok": true}')
        self.assertEqual(parse_transcript('{"text":" 你好 "}'), "你好")

    def test_health_probe_drives_supports_audio(self):
        server = MockWhisperServer().start()
        provider = WhisperProvider(base_url=server.base_url)
        try:
            info = provider.probe()
            self.assertTrue(info["ok"])
            self.assertTrue(provider.supports_audio())
            self.assertTrue(provider.model_info().supports_audio)
            self.assertEqual(provider.model_info().state, "READY")
        finally:
            server.stop()
        provider.probe()                      # 服务停了 → 明确不可用
        self.assertFalse(provider.supports_audio())
        self.assertEqual(provider.model_info().state, "ERROR")

    def test_supports_audio_reprobes_after_interval(self):
        original = ap and None
        from adapter.model import whisper as whisper_module
        provider = WhisperProvider(base_url="http://127.0.0.1:1")
        self.assertFalse(provider.supports_audio())
        server = MockWhisperServer().start()
        try:
            provider.base_url = server.base_url
            whisper_module.REPROBE_INTERVAL_SECONDS = 0.0   # 立刻允许再探
            self.assertTrue(provider.supports_audio())
        finally:
            whisper_module.REPROBE_INTERVAL_SECONDS = 5.0
            server.stop()
        self.assertIsNone(original)

    def test_chat_is_not_supported(self):
        provider = WhisperProvider(base_url="http://127.0.0.1:1")
        with self.assertRaises(WhisperError):
            provider.chat([{"role": "user", "content": "hi"}])
        self.assertFalse(provider.supports_text())
        self.assertFalse(provider.supports_vision())

    def test_multipart_builder_shape(self):
        body = build_multipart({"language": "zh"}, "file", "a.wav", b"RIFFxx", "BOUND")
        self.assertTrue(body.startswith(b"--BOUND\r\n"))
        self.assertTrue(body.endswith(b"\r\n--BOUND--\r\n"))
        self.assertIn(b'filename="a.wav"', body)
        self.assertIn(b"RIFFxx", body)


class AudioPreprocessTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ma-pre-")
        self._orig_tempdir = ap.tempfile.gettempdir
        ap.tempfile.gettempdir = lambda: self.tmpdir      # 临时文件落在测试目录

    def tearDown(self):
        ap.tempfile.gettempdir = self._orig_tempdir

    def leftovers(self):
        return [name for name in os.listdir(self.tmpdir) if name.startswith("mobile-agent-audio-")]

    def fake_ffmpeg(self, mode="ok"):
        path = os.path.join(self.tmpdir, "ffmpeg-%s" % mode)
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

    def test_target_wav_is_used_as_is(self):
        wav = write_wav(os.path.join(self.tmpdir, "ok.wav"))
        prepared = ap.ensure_wav(wav, ffmpeg_bin="/nonexistent/ffmpeg")
        self.assertTrue(prepared.ok)
        self.assertEqual(prepared.path, wav)
        self.assertFalse(prepared.temporary)          # 调用方的文件不删
        self.assertIsNone(prepared.cleanup() or None)
        self.assertTrue(os.path.isfile(wav))

    def test_non_wav_is_converted_to_temp_wav_and_cleaned(self):
        source = os.path.join(self.tmpdir, "voice.amr")
        with open(source, "wb") as handle:
            handle.write(b"#!AMR\n fake")
        prepared = ap.ensure_wav(source, ffmpeg_bin=self.fake_ffmpeg())
        self.assertTrue(prepared.ok, prepared.error)
        self.assertTrue(prepared.temporary)
        self.assertTrue(ap.is_target_wav(prepared.path))
        self.assertTrue(os.path.isfile(source))       # 原始文件不动
        prepared.cleanup()
        self.assertEqual(self.leftovers(), [])

    def test_missing_ffmpeg_is_reported(self):
        source = os.path.join(self.tmpdir, "voice.amr")
        with open(source, "wb") as handle:
            handle.write(b"#!AMR\n fake")
        prepared = ap.ensure_wav(source, ffmpeg_bin="/nonexistent/ffmpeg")
        self.assertFalse(prepared.ok)
        self.assertIn("audio_preprocess_unavailable", prepared.error)
        self.assertEqual(self.leftovers(), [])

    def test_ffmpeg_failure_is_reported(self):
        source = os.path.join(self.tmpdir, "broken.amr")
        with open(source, "wb") as handle:
            handle.write(b"not audio at all")
        prepared = ap.ensure_wav(source, ffmpeg_bin=self.fake_ffmpeg("fail"))
        self.assertFalse(prepared.ok)
        self.assertIn("audio_convert_failed", prepared.error)
        self.assertEqual(self.leftovers(), [])

    def test_missing_source_file(self):
        prepared = ap.ensure_wav(os.path.join(self.tmpdir, "nope.amr"))
        self.assertFalse(prepared.ok)
        self.assertIn("audio_not_found", prepared.error)

    def test_empty_source(self):
        prepared = ap.ensure_wav("   ")
        self.assertFalse(prepared.ok)
        self.assertIn("audio_empty", prepared.error)

    def test_remote_audio_is_downloaded_then_cleaned(self):
        with open(write_wav(os.path.join(self.tmpdir, "remote.wav")), "rb") as handle:
            wav_bytes = handle.read()
        server = MockWhisperServer(raw_body=wav_bytes).start()
        try:
            url = server.base_url + "/voice.wav"
            prepared = ap.ensure_wav(url, ffmpeg_bin="/nonexistent/ffmpeg")
            self.assertTrue(prepared.ok, prepared.error)
            self.assertTrue(prepared.temporary)
            self.assertTrue(ap.is_target_wav(prepared.path))
            prepared.cleanup()
            self.assertEqual(self.leftovers(), [])
        finally:
            server.stop()

    def test_remote_download_failure(self):
        prepared = ap.ensure_wav("http://127.0.0.1:1/voice.wav", timeout=3)
        self.assertFalse(prepared.ok)
        self.assertIn("audio_download_failed", prepared.error)


if __name__ == "__main__":
    unittest.main()
