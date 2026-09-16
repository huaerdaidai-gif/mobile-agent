# -*- coding: utf-8 -*-
"""测试用：OpenAI 兼容的假模型服务（可选流式、可选工具调用、可注入错误）。"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        server = self.server
        if self.path.startswith("/v1/models"):
            if server.fail:
                self._json(500, {"error": {"message": "mock failure"}})
                return
            self._json(200, {"object": "list", "data": [
                {"id": server.model_name, "object": "model",
                 "meta": {"n_ctx": server.context, "n_ctx_train": 32768}}]})
        elif self.path.startswith("/props"):
            if server.fail:
                self._json(500, {"error": {"message": "mock failure"}})
                return
            self._json(200, {"default_generation_settings": {"n_ctx": server.context},
                             "modalities": server.modalities, "build_info": "mock-b1"})
        else:
            self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        server = self.server
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json(400, {"error": {"message": "bad json"}})
            return
        server.calls.append(body)
        if server.fail:
            self._json(500, {"error": {"message": "mock failure"}})
            return
        content = server.reply_for(body)
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            def send_chunk(payload: bytes):
                """按 chunked 编码写一段（长度必须包含全部字节，requests 校验很严）。"""
                self.wfile.write(b"%x\r\n" % len(payload) + payload + b"\r\n")
                self.wfile.flush()

            try:
                for index in range(0, len(content), 8):
                    piece = content[index:index + 8]
                    data = {"choices": [{"index": 0, "delta": {"content": piece}}]}
                    send_chunk(("data: " + json.dumps(data, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    if server.delay:
                        time.sleep(server.delay)
                final = json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                                    "timings": {"cache_n": 10, "prompt_n": 5,
                                                "predicted_n": 7}}).encode("utf-8")
                send_chunk(b"data: " + final + b"\r\n")
                send_chunk(b"data: [DONE]\n\n")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # 客户端提前断开属于正常情况
        else:
            self._json(200, {
                "id": "mock", "object": "chat.completion", "model": server.model_name,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 42, "completion_tokens": 8, "total_tokens": 50},
                "timings": {"cache_n": 30, "prompt_n": 12, "predicted_n": 8},
            })


class MockModelServer(ThreadingHTTPServer):
    """可直接启动/停止的假模型服务。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, context: int = 2048, reply: str = "模拟回答。",
                 tool_calls=None, fail: bool = False, delay: float = 0.0,
                 model_name: str = "mock-model", port: int = 0, tool_call_rules=None,
                 echo_tool_result: bool = False, modalities=None):
        ThreadingHTTPServer.__init__(self, ("127.0.0.1", port), _Handler)
        self.context = context
        # 模拟 llama.cpp /props 的能力申报（默认全不支持）
        self.modalities = dict(modalities or {"vision": False, "audio": False, "video": False})
        self.reply = reply
        self.tool_calls = list(tool_calls or [])
        # {关键词: 工具调用 JSON}：问题里出现关键词就返回对应工具调用
        self.tool_call_rules = dict(tool_call_rules or {})
        self.echo_tool_result = echo_tool_result
        self.fail = fail
        self.delay = delay
        self.model_name = model_name
        self.calls = []
        self._thread = None

    # ---- 生命周期 ----

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
    def port(self) -> int:
        return self.server_address[1]

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:%d/v1" % self.port

    # ---- 行为 ----

    def reply_for(self, body: dict) -> str:
        """第一条回复可以是工具调用，之后的回复是普通文本。"""
        messages = body.get("messages") or []
        is_tool_result = bool(messages) and str(messages[-1].get("content", "")).startswith("工具结果：")
        if self.tool_call_rules and not is_tool_result:
            question = ""
            for message in reversed(messages):
                content = str(message.get("content", ""))
                if message.get("role") == "user" and not content.startswith("工具结果："):
                    question = content
                    break
            for keyword, call in self.tool_call_rules.items():
                if keyword in question:
                    return call
        if self.tool_calls and not is_tool_result:
            return self.tool_calls.pop(0)
        if is_tool_result and self.echo_tool_result:
            payload = str(messages[-1].get("content", ""))[len("工具结果："):]
            return "已收到工具结果：%s" % payload[:120]
        return self.reply

    def handle_error(self, request, client_address):
        """客户端提前断开（流式测试常见）不打印堆栈。"""
        return
