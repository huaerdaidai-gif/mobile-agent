# -*- coding: utf-8 -*-
"""Web 工具共用的轻量 HTTP + HTML 清洗（只用标准库）。

安全约定：
  - 只允许 http/https；
  - 强制超时 + 响应体上限（防止内存和时间被吃掉）；
  - 返回的网页文本一律视为 UNTRUSTED_CONTENT，调用方负责打标记。
"""

import html as html_module
import re
import urllib.error
import urllib.request

DEFAULT_UA = "MobileAgent/0.26 (Termux)"
DEFAULT_MAX_BYTES = 256 * 1024

_DROP_BLOCKS = re.compile(
    r"<(script|style|noscript|svg|nav|footer|header|aside|form|iframe|template)"
    r"[^>]*>.*?</\1\s*>", re.S | re.I)
_COMMENTS = re.compile(r"<!--.*?-->", re.S)
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\r\f\v\u00a0]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def is_http_url(url: str) -> bool:
    return bool(url) and url.strip().lower().startswith(("http://", "https://"))


def fetch_text(url: str, timeout: int = 10, max_bytes: int = DEFAULT_MAX_BYTES,
               headers: dict = None):
    """GET 一个 URL，返回 (ok, text, error)。永不抛异常。"""
    if not is_http_url(url):
        return False, "", "只支持 http/https 链接"
    request_headers = {"User-Agent": DEFAULT_UA,
                       "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url.strip(), headers=request_headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        return False, "", "HTTP %s" % exc.code
    except Exception as exc:  # 超时 / DNS / TLS 等
        return False, "", str(exc)[:160]
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
    return True, raw.decode(charset, "replace"), ""


def html_to_text(html: str, limit: int = 2500) -> str:
    """把 HTML 压成纯文本：丢掉脚本/样式/导航/页脚等噪声，并限制长度。"""
    if not html:
        return ""
    text = _COMMENTS.sub(" ", html)
    text = _DROP_BLOCKS.sub(" ", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|h[1-6]|tr)>", "\n", text, flags=re.I)
    text = _TAGS.sub(" ", text)
    text = html_module.unescape(text)
    text = _SPACES.sub(" ", text)
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    text = _BLANK_LINES.sub("\n\n", text)
    if limit and len(text) > limit:
        text = text[:limit] + "\n...(内容过长，已截断)"
    return text.strip()


def extract_title(html: str) -> str:
    """取 <title> 文本。"""
    match = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    return html_module.unescape(_TAGS.sub("", match.group(1)).strip()) if match else ""
