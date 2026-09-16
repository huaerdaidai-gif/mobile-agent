# -*- coding: utf-8 -*-
"""QQ Bot 适配器（OneBot v11 HTTP 模式）。

链路：QQ → OneBot 实现（NapCat/Lagrange）→ HTTP POST 事件到本 Gateway
      → Gateway → Agent Core → Model → 回复 → 调用 OneBot HTTP API 发回 QQ。

特点：
  - 只用标准库（urllib），不引入 QQ SDK；
  - 完全可替换：换 Telegram/Discord 时只写一个新的 GatewayAdapter；
  - 不支持流式（OneBot 没有流式消息接口），因此把流式回调收集起来一次性发送。
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from gateway.base import ChannelMessage, GatewayAdapter
from runtime import logging as agent_log

# 纯图片消息（没有文字）时补的固定问句：让 Agent 有事可做，而不是把消息丢掉。
# 只在「没有有效文字 + 至少有一个有效图片」时使用，不影响任何文字消息。
DEFAULT_IMAGE_TEXT = "请描述这张图片。"


class QQBotAdapter(GatewayAdapter):
    """OneBot v11 适配器。"""

    name = "qq-onebot"

    def __init__(self, config: dict, timeout: int = 10):
        qq = (config or {}).get("qq") or {}
        self.enabled = bool(qq.get("enabled", False))
        self.api_base = str(qq.get("api_base", "http://127.0.0.1:3000")).rstrip("/")
        self.token = str(qq.get("token", "") or "")
        self.self_id = str(qq.get("self_id", "") or "")
        self.reply_group = bool(qq.get("reply_group", True))
        self.require_at = bool(qq.get("require_at", True))
        self.at_sender = bool(qq.get("at_sender", False))
        self.max_chars = int(qq.get("max_chars", 1200) or 1200)
        allow = str(qq.get("allow_users", "") or "")
        self.allow_users = [item.strip() for item in allow.split(",") if item.strip()]
        self.timeout = timeout
        self.last_error = None

    # ---------------- 事件解析 ----------------

    def parse_event(self, payload: dict):
        """把 OneBot 事件转成 ChannelMessage；不是需要处理的消息则返回 None。"""
        if not isinstance(payload, dict) or payload.get("post_type") != "message":
            return None
        user_id = str(payload.get("user_id", ""))
        if not user_id or user_id == self.self_id:
            return None
        if self.allow_users and user_id not in self.allow_users:
            agent_log.log("QQ", "忽略未授权用户的消息", user=user_id)
            return None

        text, mentioned = self._extract_text(payload.get("message"), payload.get("raw_message", ""))
        images = self._extract_images(payload.get("message"))
        message_type = str(payload.get("message_type", "private"))
        if message_type == "group":
            if not self.reply_group:
                return None
            if self.require_at and not mentioned:
                return None
            session_id = "qq:group:%s" % payload.get("group_id")
            reply_to = {"type": "group", "id": str(payload.get("group_id"))}
        else:
            session_id = "qq:private:%s" % user_id
            reply_to = {"type": "private", "id": user_id}

        text = text.strip()
        if not text:
            if not images:
                return None           # 既没有文字也没有可用图片：仍然丢弃
            text = DEFAULT_IMAGE_TEXT  # 纯图片消息：补固定问句后继续走同一条链路
        return ChannelMessage(channel="qq", session_id=session_id, user_id=user_id,
                              text=text, raw=payload, reply_to=reply_to,
                              attachments={"images": images} if images else {})

    def _extract_text(self, message, raw_message: str):
        """从 OneBot 消息段里取纯文本，并判断有没有 @ 到机器人。"""
        if not isinstance(message, list):
            return (raw_message or ""), True  # 老格式：整条都是文本
        parts, mentioned = [], False
        for segment in message:
            if not isinstance(segment, dict):
                continue
            seg_type = segment.get("type")
            data = segment.get("data") or {}
            if seg_type == "text":
                parts.append(str(data.get("text", "")))
            elif seg_type == "at":
                target = str(data.get("qq", ""))
                if target and (not self.self_id or target == self.self_id):
                    mentioned = True
                else:
                    parts.append(" ")  # 别人的 @ 当成空白
        return "".join(parts), mentioned

    @staticmethod
    def _extract_images(message) -> list:
        """从 OneBot 消息段里收集图片引用：data.url 优先，data.file 兜底，空值跳过。

        - 只读 image 段，保持原顺序，不去重、不截断（多图策略由 Agent 决定）；
        - 不猜路径、不下载、不转换、不访问文件系统，原样把 url / file 交给 Agent。
        """
        if not isinstance(message, list):
            return []          # 老格式（整条是文本 / CQ 字符串）里不解析图片
        images = []
        for segment in message:
            if not isinstance(segment, dict) or segment.get("type") != "image":
                continue
            data = segment.get("data") or {}
            source = str(data.get("url", "") or "").strip() \
                or str(data.get("file", "") or "").strip()
            if source:
                images.append(source)
        return images

    # ---------------- 主流程 ----------------

    def handle(self, payload: dict, service, on_text=None):
        """收到一条 OneBot 事件：解析 → 交给 Agent → 回复。"""
        message = self.parse_event(payload)
        if message is None:
            return None
        agent_log.log("QQ", "收到消息", session=message.session_id, user=message.user_id,
                      chars=len(message.text))
        collected = []

        def collect(chunk):
            collected.append(chunk)
            if on_text:
                on_text(chunk)

        # attachments 原样透传（图片由 Agent 的 Vision 链路处理，QQ 侧不做任何理解）
        reply = service.ask(message.text, session_id=message.session_id, on_text=collect,
                            attachments=message.attachments)
        if not reply:
            return None
        self.send_reply(message, reply)
        return reply

    def send_reply(self, message: ChannelMessage, text: str) -> dict:
        """把回复发回 QQ（过长自动分片）。"""
        prefix = ""
        if message.reply_to.get("type") == "group" and self.at_sender and message.user_id:
            prefix = "[CQ:at,qq=%s] " % message.user_id
        results = []
        for chunk in self._split(text):
            results.append(self.send(message.reply_to, prefix + chunk))
            prefix = ""  # 只有第一片带 @
        return {"ok": all(item.get("ok") for item in results), "parts": len(results)}

    def _split(self, text: str):
        """按 max_chars 分片，尽量不切断行。"""
        text = text or ""
        if len(text) <= self.max_chars:
            return [text]
        chunks, current = [], ""
        for line in text.splitlines(True):
            if len(current) + len(line) > self.max_chars and current:
                chunks.append(current)
                current = ""
            while len(line) > self.max_chars:  # 单行过长时硬切
                chunks.append(line[:self.max_chars])
                line = line[self.max_chars:]
            current += line
        if current:
            chunks.append(current)
        return chunks

    # ---------------- OneBot HTTP API ----------------

    def send(self, reply_to: dict, text: str) -> dict:
        """调用 OneBot HTTP API 发消息。"""
        if reply_to.get("type") == "group":
            action = "send_group_msg"
            payload = {"group_id": reply_to.get("id"), "message": text}
        else:
            action = "send_private_msg"
            payload = {"user_id": reply_to.get("id"), "message": text}
        return self._call(action, payload)

    def _call(self, action: str, payload: dict) -> dict:
        url = "%s/%s" % (self.api_base, action)
        if self.token:
            url += "?access_token=" + urllib.parse.quote(self.token, safe="")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data,
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            self.last_error = "HTTP %s" % exc.code
            agent_log.log("QQ", "发送失败", level="WARNING", error=self.last_error)
            return {"ok": False, "error": self.last_error}
        except Exception as exc:  # 连接失败等
            self.last_error = str(exc)
            agent_log.log("QQ", "发送失败", level="WARNING", error=self.last_error)
            return {"ok": False, "error": self.last_error}
        try:
            result = json.loads(body)
        except ValueError:
            return {"ok": False, "error": "返回内容不是 JSON"}
        ok = str(result.get("status", "ok")).lower() in ("ok", "async")
        return {"ok": ok, "raw": result}

    def health(self) -> dict:
        """健康检查：问一下 OneBot 实现当前登录的是哪个账号。"""
        if not self.enabled:
            return {"ok": True, "enabled": False, "adapter": self.name,
                    "detail": "QQ 未启用（qq.enabled=false）"}
        result = self._call("get_login_info", {})
        if not result.get("ok"):
            return {"ok": False, "enabled": True, "adapter": self.name,
                    "api_base": self.api_base, "error": result.get("error") or "OneBot 无响应"}
        data = (result.get("raw") or {}).get("data") or {}
        return {"ok": True, "enabled": True, "adapter": self.name, "api_base": self.api_base,
                "bot_id": str(data.get("user_id", "")), "nickname": data.get("nickname", "")}


def build_qq_adapter(config: dict) -> QQBotAdapter:
    """按配置构造 QQ 适配器。"""
    return QQBotAdapter(config)
