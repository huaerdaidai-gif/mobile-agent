# -*- coding: utf-8 -*-
"""web_fetch 工具：抓取网页正文（纯标准库，去脚本/导航/页脚，硬性长度上限）。"""

from tools.web._http import extract_title, fetch_text, html_to_text


def web_fetch(url: str, max_chars: int = 2500, timeout: int = 10) -> dict:
    """抓取并清洗网页正文，返回 {"ok","url","title","content","chars"}。"""
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "web_fetch: url 为空"}
    ok, html, error = fetch_text(url, timeout=timeout)
    if not ok:
        return {"ok": False, "error": "web_fetch_unavailable: %s" % error}
    title = extract_title(html)
    content = html_to_text(html, limit=max_chars)
    if not content:
        return {"ok": False, "error": "web_fetch_unavailable: 正文为空（可能是纯前端页面）"}
    return {"ok": True, "url": url, "title": title, "content": content,
            "chars": len(content), "untrusted": True}
