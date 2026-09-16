# -*- coding: utf-8 -*-
"""Gateway 测试：/health、/api/chat、流式、认证、admin 拦截、QQ 事件入口。"""

import json
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from tests import helpers
from tests.mock_model import MockModelServer
from tests.mock_onebot import MockOneBotServer


def http(url, payload=None, headers=None, method=None, timeout=20):
    """发一个 HTTP 请求，返回 (状态码, 解析后的 JSON 或文本)。"""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                     headers=headers or {})
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            try:
                return response.status, json.loads(body)
            except ValueError:
                return response.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, body


class GatewayTest(unittest.TestCase):

    @staticmethod
    def wait_for(predicate, timeout=10.0, interval=0.1):
        """轮询等待条件成立（QQ 事件是「先回 200，再处理」，需要给一点时间）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return predicate()

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ma-gw-")
        self.model = MockModelServer(context=2048, reply="网关测试回复。").start()
        self.onebot = MockOneBotServer().start()

    def tearDown(self):
        if getattr(self, "server", None):
            self.server.stop()
        self.model.stop()
        self.onebot.stop()

    def start_gateway(self, **overrides):
        from gateway.server import GatewayServer
        from runtime.service import AgentService
        config = helpers.make_config(self.model.base_url, self.tmpdir, **overrides)
        config["qq"]["enabled"] = True
        config["qq"]["api_base"] = self.onebot.api_base
        service = AgentService(config=config)
        self.server = GatewayServer(service, config=config)
        state = self.server.start(block=False)
        self.base = "http://127.0.0.1:%d" % state["port"]
        return self.base

    def test_health_endpoint(self):
        base = self.start_gateway()
        status, payload = http(base + "/health")
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"], payload)
        components = payload["components"]
        for name in ("agent", "model", "gateway", "qq", "host", "capability"):
            self.assertIn(name, components)
        self.assertTrue(components["model"]["ok"])
        self.assertEqual(components["model"]["context"], 2048)
        self.assertTrue(components["qq"]["ok"])

    def test_api_chat_returns_reply(self):
        base = self.start_gateway()
        status, payload = http(base + "/api/chat", {"text": "你好", "session_id": "t1"})
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["reply"], "网关测试回复。")
        self.assertEqual(payload["session_id"], "t1")

    def test_api_chat_missing_text(self):
        base = self.start_gateway()
        status, payload = http(base + "/api/chat", {"session_id": "t1"})
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])

    def test_api_chat_stream(self):
        base = self.start_gateway()
        status, body = http(base + "/api/chat/stream", {"text": "你好", "session_id": "s1"})
        self.assertEqual(status, 200)
        self.assertIn("网关测试回复。", body)

    def test_token_auth(self):
        base = self.start_gateway(gateway={"auth": "token", "token": "s3cret"})
        status, payload = http(base + "/api/chat", {"text": "你好"})
        self.assertEqual(status, 401, payload)
        status, payload = http(base + "/api/chat", {"text": "你好"},
                               headers={"Authorization": "Bearer s3cret"})
        self.assertEqual(status, 200, payload)

    def test_admin_disabled_by_default(self):
        base = self.start_gateway()
        status, payload = http(base + "/admin/restart", {})
        self.assertEqual(status, 403, payload)
        self.assertIn("管理接口默认关闭", payload["error"])

    def test_unknown_path_404(self):
        base = self.start_gateway()
        status, payload = http(base + "/nope")
        self.assertEqual(status, 404)

    def test_qq_event_pushes_reply(self):
        base = self.start_gateway()
        event = {"post_type": "message", "message_type": "private", "user_id": 20002,
                 "self_id": 10001, "raw_message": "你好",
                 "message": [{"type": "text", "data": {"text": "你好"}}]}
        status, payload = http(base + "/qq/onebot", event)
        self.assertEqual(status, 200)
        self.assertEqual(payload.get("status"), "ok")
        # 服务端先回 200 再处理事件，所以这里等一小会儿
        self.assertTrue(self.wait_for(lambda: self.onebot.sent_messages()),
                        "QQ 回复应该已经发出")
        sent = self.onebot.sent_messages()
        self.assertEqual(sent[0]["payload"]["message"], "网关测试回复。")

    def test_qq_event_chunked_body(self):
        """NapCat 推送用的是 chunked 编码，必须能正确解析。"""
        import socket
        base = self.start_gateway()
        host, port = base.replace("http://", "").split(":")
        event = json.dumps({"post_type": "message", "message_type": "private",
                            "user_id": 20002, "self_id": 10001, "raw_message": "分块你好",
                            "message": [{"type": "text", "data": {"text": "分块你好"}}]}).encode()
        body = b"%x\r\n" % len(event) + event + b"\r\n0\r\n\r\n"
        head = ("POST /qq/onebot HTTP/1.1\r\nHost: %s\r\nContent-Type: application/json\r\n"
                "Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n" % host).encode()
        sock = socket.create_connection((host, int(port)), timeout=10)
        sock.sendall(head + body)
        response = sock.recv(200)
        sock.close()
        self.assertIn(b"200", response.split(b"\r\n")[0])
        self.assertTrue(self.wait_for(lambda: self.onebot.sent_messages()),
                        "chunked 事件也应该被处理并回复")

    def test_qq_event_token_check(self):
        base = self.start_gateway(qq={"token": "qq-secret", "push_token": "push-secret"})
        status, payload = http(base + "/qq/onebot", {"post_type": "message"})
        self.assertEqual(status, 401, payload)
        # 带对 token（query 形式）应该放行
        status, payload = http(base + "/qq/onebot?access_token=push-secret",
                               {"post_type": "message"})
        self.assertEqual(status, 200, payload)

    def test_qq_event_without_push_token_is_accepted(self):
        base = self.start_gateway(qq={"token": "qq-secret", "push_token": ""})
        status, payload = http(base + "/qq/onebot", {"post_type": "message"})
        self.assertEqual(status, 200, payload)


if __name__ == "__main__":
    unittest.main()
