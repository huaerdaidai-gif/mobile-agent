# -*- coding: utf-8 -*-
"""HTTP Gateway（标准库实现，不引入 Web 框架）。

路由：
    GET  /                 简单索引
    GET  /health           健康检查（Agent / Model / Gateway / QQ / Host / Capability）
    POST /api/chat         {"text": "...", "session_id": "..."} → {"reply": "..."}
    POST /api/chat/stream  同样入参，返回流式纯文本（读到 EOF 结束）
    POST /qq/onebot        OneBot v11 事件推送入口（QQ → Gateway）
    POST /admin/<action>   管理接口：默认关闭，由 PolicyGate 拦截

线程模型：ThreadingHTTPServer，每个请求一个线程（服务端本就该如此），
Agent 内部不创建任何后台线程。
"""

import json
import signal
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gateway import health as gateway_health
from gateway.base import build_auth
from runtime import config as runtime_config
from runtime import logging as agent_log

MAX_BODY_BYTES = 64 * 1024  # 请求体上限，防止超大 payload


class GatewayHTTPServer(ThreadingHTTPServer):
    """带 service / auth / QQ 引用的 HTTP 服务器。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, service, auth, config):
        ThreadingHTTPServer.__init__(self, address, GatewayRequestHandler)
        self.service = service
        self.auth = auth
        self.config = config
        self.admin_enabled = bool(runtime_config.get(config, "extension.admin_enabled", False))
        # 事件推送校验用的 token（与调用 OneBot API 用的 qq.token 分开配置）
        self.qq_push_token = str(runtime_config.get(config, "qq.push_token", "") or "")
        self.auth_mode = (config.get("gateway") or {}).get("auth", "none")

    def state(self) -> dict:
        host, port = self.server_address[0], self.server_address[1]
        return {"ok": True, "listening": True, "host": host, "port": port,
                "auth": self.auth_mode, "adapter": "http"}


class GatewayRequestHandler(BaseHTTPRequestHandler):
    """所有路由的实现。"""

    protocol_version = "HTTP/1.1"
    server_version = "mobile-agent-gateway"

    # ---------------- 工具方法 ----------------

    def log_message(self, fmt, *args):
        """默认会往 stderr 打访问日志，这里改成统一日志。"""
        agent_log.log("GATEWAY", "access %s" % (fmt % args), level="DEBUG")

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _read_body(self) -> bytes:
        """读取请求体：同时支持 Content-Length 和 Transfer-Encoding: chunked。

        NapCat 推送事件时用的是 chunked 编码，只按 Content-Length 读会拿到空 body
        （事件被静默丢弃，但 HTTP 层看起来是 200 成功——很难发现）。
        """
        encoding = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in encoding:
            chunks = []
            total = 0
            while True:
                line = self.rfile.readline(64).strip()
                if not line:
                    break
                try:
                    size = int(line.split(b";")[0], 16)
                except ValueError:
                    break
                if size == 0:
                    self.rfile.readline(8)
                    break
                if total + size > MAX_BODY_BYTES:
                    break
                chunks.append(self.rfile.read(size))
                total += size
                self.rfile.read(2)  # 每块结尾的 CRLF
            return b"".join(chunks)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return b""
        if length <= 0 or length > MAX_BODY_BYTES:
            return b""
        return self.rfile.read(length)

    def _read_json(self) -> dict:
        raw = self._read_body()
        try:
            data = json.loads(raw.decode("utf-8", "replace") or "{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _query(self) -> dict:
        parsed = urllib.parse.urlparse(self.path)
        return {key: value[0] for key, value in urllib.parse.parse_qs(parsed.query).items()}

    def _path(self) -> str:
        return urllib.parse.urlparse(self.path).path.rstrip("/") or "/"

    def _headers(self) -> dict:
        return {key: value for key, value in self.headers.items()}

    # ---------------- 路由 ----------------

    def do_GET(self):
        path = self._path()
        if path in ("/", "/index"):
            self._send_json(200, {"service": "mobile-agent gateway",
                                  "routes": ["/health", "/api/chat", "/api/chat/stream", "/qq/onebot"]})
        elif path == "/health":
            self._health()
        else:
            self._send_json(404, {"ok": False, "error": "未知路径：%s" % path})

    def do_POST(self):
        path = self._path()
        if path == "/api/chat":
            self._api_chat(stream=False)
        elif path == "/api/chat/stream":
            self._api_chat(stream=True)
        elif path == "/qq/onebot":
            self._qq_event()
        elif path.startswith("/admin"):
            self._admin(path)
        else:
            self._send_json(404, {"ok": False, "error": "未知路径：%s" % path})

    # ---------------- 各路由实现 ----------------

    def _health(self):
        server = self.server
        probe = self._query().get("fast") not in ("1", "true")
        payload = gateway_health.collect(server.service, gateway_state=server.state(),
                                         model_probe=probe)
        self._send_json(200 if payload.get("ok") else 503, payload)

    def _authorized(self) -> tuple:
        return self.server.auth.authorize(self._headers())

    def _api_chat(self, stream: bool):
        ok, identity = self._authorized()
        if not ok:
            self._send_json(401, {"ok": False, "error": (identity or {}).get("error", "认证失败")})
            return
        body = self._read_json()
        text = str(body.get("text") or body.get("message") or "").strip()
        session_id = str(body.get("session_id") or identity.get("identity") or "api:default")
        if not text:
            self._send_json(400, {"ok": False, "error": "缺少 text 字段"})
            return

        if stream:
            # 无 Content-Length + 关闭连接：客户端可以边读边处理
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            def emit(chunk):
                try:
                    self.wfile.write(chunk.encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            try:
                self.server.service.ask(text, session_id=session_id, on_text=emit)
            except Exception as exc:  # 流已开始，只能把错误写进响应体
                emit("\n[错误] %s" % exc)
            return

        try:
            reply = self.server.service.ask(text, session_id=session_id)
        except Exception as exc:
            self._send_json(502, {"ok": False, "error": str(exc)[:300], "session_id": session_id})
            return
        self._send_json(200, {"ok": True, "reply": reply, "session_id": session_id})

    def _qq_event(self):
        """OneBot 事件推送：先回 200（避免对方超时重推），再在当前线程里处理。"""
        server = self.server
        if not getattr(server.service.qq_adapter, "enabled", False):
            self._send_json(200, {"ok": False, "error": "QQ 未启用（qq.enabled=false）"})
            return
        headers = self._headers()
        if server.qq_push_token:
            supplied = self._supplied_token(headers)
            agent_log.log("QQ", "收到事件推送", level="DEBUG",
                          token_found=bool(supplied), ua=headers.get("User-Agent", ""))
            if supplied != server.qq_push_token:
                self._send_json(401, {"ok": False, "error": "QQ 事件 token 校验失败"})
                return
        else:
            agent_log.log("QQ", "收到事件推送（未配置 push_token，按本机信任处理）", level="DEBUG")
        payload = self._read_json()
        # 回一个标准 OneBot 响应（空快速操作），并且立刻关闭连接：
        # NapCat 的 HTTP 客户端对响应格式很挑，长期占用连接还会让它解析失败
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Connection", "close")
        body = json.dumps({"status": "ok", "retcode": 0, "data": None}).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except BrokenPipeError:
            pass
        self.close_connection = True

        # 事件处理（模型调用可能几十秒）放到短命线程里，避免占着这条连接
        def _worker():
            try:
                server.service.qq_adapter.handle(payload, server.service)
            except Exception as exc:  # 事件处理失败也不能影响网关
                agent_log.log("QQ", "事件处理失败：%s" % exc, level="ERROR")

        threading.Thread(target=_worker, name="qq-event", daemon=True).start()

    def _supplied_token(self, headers: dict) -> str:
        """从多种常见位置取 token：?access_token= / Authorization(Bearer) / X-Auth-Token / token。"""
        supplied = self._query().get("access_token", "")
        for key, value in (headers or {}).items():
            lowered = key.lower()
            value = str(value).strip()
            if lowered == "authorization":
                supplied = supplied or (value[7:].strip() if value.lower().startswith("bearer ") else value)
            elif lowered in ("x-auth-token", "x-self-token", "token"):
                supplied = supplied or value
        return supplied

    def _admin(self, path: str):
        """管理接口占位：默认关闭，未来走 Authentication → Authorization → Policy → Executor。"""
        action = path[len("/admin"):].strip("/") or "root"
        ok, identity = self._authorized()
        if not ok:
            self._send_json(401, {"ok": False, "error": "认证失败"})
            return
        decision = self.server.service.extensions.policy.allow("admin." + action, identity)
        if not decision.get("allowed"):
            self._send_json(403, {"ok": False, "error": decision.get("reason"), "action": action})
            return
        self._send_json(200, {"ok": True, "action": action, "identity": identity,
                              "note": "管理接口尚未实现（占位）"})


class GatewayServer(object):
    """Gateway 生命周期封装（start / stop / state）。"""

    def __init__(self, service, config: dict = None):
        self.service = service
        self.config = config or service.config
        gateway_config = self.config.get("gateway") or {}
        self.host = str(gateway_config.get("host", "127.0.0.1"))
        self.port = int(gateway_config.get("port", 8787))
        self.auth = build_auth(self.config)
        self.httpd = None
        self._thread = None

    def build(self) -> GatewayHTTPServer:
        self.httpd = GatewayHTTPServer((self.host, self.port), self.service, self.auth, self.config)
        self.service.gateway_state = self.httpd.state()
        return self.httpd

    def start(self, block: bool = True) -> dict:
        """启动网关；block=True 时阻塞在 serve_forever。"""
        if self.httpd is None:
            self.build()
        state = self.httpd.state()
        agent_log.log("GATEWAY", "启动监听 http://%s:%s（认证=%s）"
                      % (state["host"], state["port"], state["auth"]))
        if block:
            try:
                self.httpd.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                self.stop()
            return state
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="gateway", daemon=True)
        self._thread.start()
        return state

    def stop(self) -> None:
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
            self.httpd = None
        self.service.gateway_state = {}
        agent_log.log("GATEWAY", "已停止")


def build_server(config: dict = None, service=None) -> GatewayServer:
    """按配置构造 Gateway（service 省略时自己建一个）。"""
    if service is None:
        from runtime.service import AgentService
        service = AgentService(config=config)
    return GatewayServer(service, config=config or service.config)


def main() -> int:
    """python3 -m gateway.server：前台启动网关（CLI 的 start 会调它）。"""
    import argparse

    from runtime.service import AgentService

    parser = argparse.ArgumentParser(prog="gateway.server")
    parser.add_argument("--config", default=None, help="config.yaml 路径")
    args = parser.parse_args()
    config = runtime_config.load(args.config)
    service = AgentService(config=config)
    server = GatewayServer(service, config=config)

    def _shutdown(_signum, _frame):
        agent_log.log("GATEWAY", "收到退出信号，正在关闭")
        server.stop()
        raise SystemExit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _shutdown)
        except (ValueError, OSError):
            pass
    server.start(block=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
