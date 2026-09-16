# -*- coding: utf-8 -*-
"""测试用：假的 OneBot v11 HTTP 实现（记录被调用的接口与消息内容）。"""

import json
import threading
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

    def do_POST(self):
        server = self.server
        if server.token:
            supplied = ""
            if "access_token=" in self.path:
                supplied = self.path.split("access_token=", 1)[1].split("&", 1)[0]
            if supplied != server.token:
                self._json(403, {"status": "failed", "message": "token 校验失败"})
                return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        action = self.path.split("?")[0].strip("/")
        server.calls.append({"action": action, "payload": body})
        if action == "get_login_info":
            self._json(200, {"status": "ok", "retcode": 0,
                             "data": {"user_id": 10001, "nickname": "mock-bot"}})
        elif action in ("send_private_msg", "send_group_msg"):
            self._json(200, {"status": "ok", "retcode": 0, "data": {"message_id": len(server.calls)}})
        else:
            self._json(404, {"status": "failed", "message": "unsupported action"})


class MockOneBotServer(ThreadingHTTPServer):
    """假的 OneBot HTTP API。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, token: str = "", port: int = 0):
        ThreadingHTTPServer.__init__(self, ("127.0.0.1", port), _Handler)
        self.token = token
        self.calls = []
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
    def api_base(self) -> str:
        return "http://127.0.0.1:%d" % self.server_address[1]

    def sent_messages(self):
        return [call for call in self.calls if call["action"].startswith("send_")]
