# -*- coding: utf-8 -*-
"""图片工具（Stage 2）。

职责边界（严格按架构要求）：
  - 本工具只负责：图片 URL/路径元信息、下载保存、为未来视觉模型准备输入；
  - 绝不「理解」图片内容——那必须交给 Vision 模型；
  - 当前没有视觉模型：image_analyze 返回 vision_unavailable，不假装完成；
  - 绝不把图片 base64 塞进 text-only 的 Qwen 提示词。
"""

import mimetypes
import os
import urllib.error
import urllib.request

from tools.system.file import PROJECT_ROOT, _relative, _resolve
from tools.web._http import DEFAULT_UA, is_http_url

MAX_IMAGE_BYTES = 5 * 1024 * 1024


def image_info(path_or_url: str, timeout: int = 10) -> dict:
    """图片元信息：本地路径给大小/类型；URL 给 Content-Type/Content-Length。"""
    target = (path_or_url or "").strip()
    if not target:
        return {"ok": False, "error": "image_info: 需要图片路径或 URL"}
    if is_http_url(target):
        request = urllib.request.Request(target, headers={"User-Agent": DEFAULT_UA}, method="HEAD")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return {"ok": True, "source": "url", "url": target,
                        "content_type": response.headers.get("Content-Type", ""),
                        "bytes": int(response.headers.get("Content-Length") or 0)}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "error": "image_info: HTTP %s" % exc.code}
        except Exception as exc:
            return {"ok": False, "error": "image_info: %s" % str(exc)[:120]}
    try:
        full = _resolve(target)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not os.path.isfile(full):
        return {"ok": False, "error": "图片不存在：%s" % _relative(full)}
    guessed, _encoding = mimetypes.guess_type(full)
    return {"ok": True, "source": "file", "path": _relative(full),
            "content_type": guessed or "", "bytes": os.path.getsize(full)}


def image_download(url: str, path: str, timeout: int = 15,
                   max_bytes: int = MAX_IMAGE_BYTES) -> dict:
    """下载图片到项目目录内（大小上限 5MB，路径同样受限）。"""
    if not is_http_url(url):
        return {"ok": False, "error": "image_download: 只支持 http/https 链接"}
    try:
        full = _resolve(path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    request = urllib.request.Request(url.strip(), headers={"User-Agent": DEFAULT_UA}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": "image_download: HTTP %s" % exc.code}
    except Exception as exc:
        return {"ok": False, "error": "image_download: %s" % str(exc)[:120]}
    if len(raw) > max_bytes:
        return {"ok": False, "error": "image_download: 图片超过 %d KB 上限" % (max_bytes // 1024)}
    try:
        parent = os.path.dirname(full)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        with open(full, "wb") as handle:
            handle.write(raw)
    except OSError as exc:
        return {"ok": False, "error": "image_download: 写入失败 %s" % exc}
    return {"ok": True, "path": _relative(full), "bytes": len(raw)}


def image_prepare(path_or_url: str) -> dict:
    """为未来 Vision 模型准备输入（只给出引用与大小，不返回 base64）。"""
    info = image_info(path_or_url)
    if not info.get("ok"):
        return info
    return {"ok": True, "reference": info.get("path") or info.get("url"),
            "content_type": info.get("content_type", ""), "bytes": info.get("bytes", 0),
            "note": "该引用可直接交给 Vision 模型；当前 vision = UNAVAILABLE"}


def image_analyze(path_or_url: str = None, question: str = None) -> dict:
    """图片理解：必须由 Vision 模型完成；当前没有视觉模型。

    注意（v0.27 边界）：图片理解已经由 Agent 的 Vision stage 直接承担
    （model/router.py 判定 VISION → agent._vision_stage 调视觉 provider）。
    本工具刻意**不**再触发一次视觉推理，避免同一张图被推理两遍。
    """
    return {"ok": False, "error": "vision_unavailable: 当前未部署视觉模型（llama.cpp /props "
                                  "modalities.vision=false），无法理解图片内容"}


__all__ = ["image_info", "image_download", "image_prepare", "image_analyze",
           "MAX_IMAGE_BYTES", "PROJECT_ROOT"]
