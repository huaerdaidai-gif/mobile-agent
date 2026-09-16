# -*- coding: utf-8 -*-
"""web_search 工具：轻量网页搜索（DuckDuckGo HTML 版，无需 API Key）。

只用标准库：请求 HTML → 正则提取标题/链接/摘要 → 只保留前 N 条。
搜索源不可用时如实返回错误（web_search_unavailable），不编造结果。
"""

import re
import urllib.parse

from tools.web._http import fetch_text, html_to_text

SEARCH_URL = "https://html.duckduckgo.com/html/?q={query}"

_RESULT_LINK = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
_SNIPPET = re.compile(
    r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', re.S | re.I)


def clean_url(url: str) -> str:
    """把 DuckDuckGo 的跳转链接还原成真实链接。"""
    if url.startswith("//"):
        url = "https:" + url
    if "duckduckgo.com/l/" in url and "uddg=" in url:
        query = urllib.parse.urlparse(url).query
        target = urllib.parse.parse_qs(query).get("uddg")
        if target:
            return target[0]
    return url


def parse_results(html: str, limit: int = 5, snippet_chars: int = 160) -> list:
    """从搜索页 HTML 里提取结果列表（离线可测）。"""
    links = _RESULT_LINK.findall(html or "")
    snippets = _SNIPPET.findall(html or "")
    results = []
    for index, (url, title_html) in enumerate(links[:max(1, int(limit))]):
        snippet_html = snippets[index] if index < len(snippets) else ""
        results.append({"title": html_to_text(title_html, 120),
                        "url": clean_url(url),
                        "snippet": html_to_text(snippet_html, snippet_chars)})
    return results


def web_search(query: str, limit: int = 5, timeout: int = 10,
               snippet_chars: int = 160) -> dict:
    """搜索并返回最多 limit 条 {title, url, snippet}。"""
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "web_search: query 为空"}
    ok, text, error = fetch_text(SEARCH_URL.format(query=urllib.parse.quote(query)),
                                 timeout=timeout, max_bytes=512 * 1024)
    if not ok:
        return {"ok": False, "error": "web_search_unavailable: %s" % error}
    results = parse_results(text, limit=limit, snippet_chars=snippet_chars)
    if not results:
        return {"ok": False, "error": "web_search_unavailable: 结果页无法解析（可能被限流）"}
    return {"ok": True, "query": query, "count": len(results), "results": results,
            "source": "duckduckgo-html", "untrusted": True}
