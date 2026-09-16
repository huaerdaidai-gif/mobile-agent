# -*- coding: utf-8 -*-
"""web_search 工具：轻量网页搜索（无需 API Key，只用标准库）。

提供方按顺序尝试，第一个成功即返回：
  1. Bing（cn.bing.com，国内可达）
  2. DuckDuckGo HTML（海外网络可用）
全部失败时如实返回 web_search_unavailable，绝不编造结果。
"""

import re
import urllib.parse

from tools.web._http import fetch_text, html_to_text

BING_URL = "https://cn.bing.com/search?q={query}&setlang=zh-CN"
DDG_URL = "https://html.duckduckgo.com/html/?q={query}"

_BING_BLOCK = re.compile(r'<li class="b_algo".*?(?=<li class="b_algo"|</ol>)', re.S | re.I)
_BING_TITLE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S | re.I)
_BING_HREF = re.compile(r'<h2[^>]*>.*?<a[^>]+href="([^"]+)"', re.S | re.I)
_BING_SNIPPET = re.compile(r"<p[^>]*>(.*?)</p>", re.S | re.I)

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


def parse_bing(html: str, limit: int = 5, snippet_chars: int = 160) -> list:
    """解析 Bing 结果页（离线可测）。"""
    results = []
    for block in _BING_BLOCK.findall(html or ""):
        title_html = _BING_TITLE.search(block)
        href = _BING_HREF.search(block)
        snippet_html = _BING_SNIPPET.search(block)
        if not (title_html and href):
            continue
        results.append({"title": html_to_text(title_html.group(1), 120),
                        "url": clean_url(href.group(1)),
                        "snippet": html_to_text(snippet_html.group(1), snippet_chars)
                                   if snippet_html else ""})
        if len(results) >= max(1, int(limit)):
            break
    return results


def parse_results(html: str, limit: int = 5, snippet_chars: int = 160) -> list:
    """解析 DuckDuckGo HTML 结果页（离线可测）。"""
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
    """搜索并返回最多 limit 条 {title, url, snippet}（Bing 优先，DDG 兜底）。"""
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "web_search: query 为空"}
    errors = []
    providers = (("bing", BING_URL, parse_bing), ("duckduckgo", DDG_URL, parse_results))
    for name, url_template, parser in providers:
        ok, text, error = fetch_text(url_template.format(query=urllib.parse.quote(query)),
                                     timeout=timeout, max_bytes=512 * 1024)
        if not ok:
            errors.append("%s: %s" % (name, error))
            continue
        results = parser(text, limit=limit, snippet_chars=snippet_chars)
        if not results:
            errors.append("%s: 结果页无法解析" % name)
            continue
        return {"ok": True, "query": query, "count": len(results), "results": results,
                "source": name, "untrusted": True}
    return {"ok": False, "error": "web_search_unavailable: %s" % "；".join(errors)}
