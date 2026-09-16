# -*- coding: utf-8 -*-
"""音乐工具（Stage 2 接口）：不硬编码不稳定的第三方站点。

当前没有可靠且合法的音乐 Provider，因此两个工具都明确返回 unavailable。
"""

UNAVAILABLE_REASON = ("music_unavailable: 当前没有配置可靠的音乐 Provider"
                      "（未硬编码任何第三方站点）")


def music_search(query: str = None) -> dict:
    """搜索音乐（未配置 Provider → 不可用）。"""
    return {"ok": False, "error": UNAVAILABLE_REASON, "query": query}


def music_play(query: str = None) -> dict:
    """播放音乐（未配置 Provider → 不可用）。"""
    return {"ok": False, "error": UNAVAILABLE_REASON, "query": query}


__all__ = ["music_search", "music_play", "UNAVAILABLE_REASON"]
