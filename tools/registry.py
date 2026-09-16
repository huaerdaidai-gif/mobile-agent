# -*- coding: utf-8 -*-
"""工具注册表（Stage 2：分组 + 按需 Schema + 统一 invoke）。

关键设计：
  1. **分组**：core / system / web / vision / audio / media / writing；
  2. **按需加载**：schema_text(groups) 只生成当前需要的组的说明，并缓存；
  3. **统一执行**：invoke(name, arguments) 分发表，Agent Core 不再写 if tool == "xxx"；
  4. **真实可用性**：available 由「配置开关 + 模型能力探测」共同决定，探测不到就是 False；
  5. **结果格式化也在这里**：format_result() 让 Core 不认识任何具体工具。

兼容性：v0.25.1 的 get_tools / get_tool / list_tools / tool_names / parameter_names /
describe_tools / describe_parameters / example_arguments / validate_arguments 全部保留。
"""

from typing import Dict, List, Optional, Tuple

# ---------------- 工具组 ----------------

GROUPS = {
    "core": {"description": "基础能力（时间）", "default_enabled": True},
    "system": {"description": "系统能力（文件 / 命令）", "default_enabled": True},
    "web": {"description": "联网能力（天气 / 搜索 / 网页正文）", "default_enabled": True},
    "writing": {"description": "把文稿保存成文件", "default_enabled": True},
    "vision": {"description": "图片元信息与理解（理解需要视觉模型）", "default_enabled": False},
    "audio": {"description": "语音转写与合成（需要音频模型）", "default_enabled": False},
    "media": {"description": "音乐检索与播放（需要可靠 Provider）", "default_enabled": False},
}

# ---------------- 工具定义 ----------------
# group       所属工具组
# risk        low / medium / high（high 需要额外确认，当前仅用于展示与策略）
# available   是否需要外部模型/服务；True 表示纯本地即可用
# needs       none / vision_model / audio_model / network / music_provider
# untrusted   结果是否来自不可信外部内容（网页）

TOOLS = {
    "time": {
        "group": "core", "description": "获取当前时间", "parameters": {}, "required": [],
        "risk": "low", "available": True, "needs": "none", "examples": [{}],
    },
    "shell": {
        "group": "system", "description": "执行允许的shell命令",
        "parameters": {"command": "string"}, "required": ["command"],
        "risk": "high", "available": True, "needs": "none",
        "examples": [{"command": "ls"}],
    },
    "file": {
        "group": "system", "description": "文件读取写入",
        "parameters": {"action": "string", "path": "string", "content": "string"},
        "required": ["action", "path"], "risk": "medium", "available": True, "needs": "none",
        "examples": [{"action": "read", "path": "README.md"}],
    },
    "weather": {
        "group": "web", "description": "查询城市天气",
        "parameters": {"location": "string"}, "required": [],
        "risk": "low", "available": True, "needs": "network",
        "examples": [{"location": "北京"}],
    },
    "web_search": {
        "group": "web", "description": "搜索网页（返回标题/链接/摘要）",
        "parameters": {"query": "string"}, "required": ["query"],
        "risk": "low", "available": True, "needs": "network", "untrusted": True,
        "examples": [{"query": "llama.cpp"}],
    },
    "web_fetch": {
        "group": "web", "description": "抓取网页正文",
        "parameters": {"url": "string"}, "required": ["url"],
        "risk": "low", "available": True, "needs": "network", "untrusted": True,
        "examples": [{"url": "https://example.com"}],
    },
    "image_info": {
        "group": "vision", "description": "查看图片元信息（路径或URL）",
        "parameters": {"path_or_url": "string"}, "required": ["path_or_url"],
        "risk": "low", "available": True, "needs": "none",
        "examples": [{"path_or_url": "pic.jpg"}],
    },
    "image_download": {
        "group": "vision", "description": "下载图片到项目目录",
        "parameters": {"url": "string", "path": "string"}, "required": ["url", "path"],
        "risk": "low", "available": True, "needs": "network",
        "examples": [{"url": "https://example.com/a.jpg", "path": "a.jpg"}],
    },
    "image_analyze": {
        "group": "vision", "description": "理解图片内容（需要视觉模型）",
        "parameters": {"path_or_url": "string", "question": "string"}, "required": [],
        "risk": "low", "available": False, "needs": "vision_model",
        "examples": [{"path_or_url": "pic.jpg", "question": "图里写了什么"}],
    },
    "speech_to_text": {
        "group": "audio", "description": "语音转文字（需要音频模型）",
        "parameters": {"path_or_url": "string"}, "required": [],
        "risk": "low", "available": False, "needs": "audio_model",
        "examples": [{"path_or_url": "voice.amr"}],
    },
    "text_to_speech": {
        "group": "audio", "description": "文字转语音（需要音频模型）",
        "parameters": {"text": "string"}, "required": ["text"],
        "risk": "low", "available": False, "needs": "audio_model",
        "examples": [{"text": "你好"}],
    },
    "music_search": {
        "group": "media", "description": "搜索音乐（需要音乐 Provider）",
        "parameters": {"query": "string"}, "required": ["query"],
        "risk": "low", "available": False, "needs": "music_provider",
        "examples": [{"query": "轻音乐"}],
    },
    "music_play": {
        "group": "media", "description": "播放音乐（需要音乐 Provider）",
        "parameters": {"query": "string"}, "required": ["query"],
        "risk": "low", "available": False, "needs": "music_provider",
        "examples": [{"query": "轻音乐"}],
    },
    "document_write": {
        "group": "writing", "description": "把已经写好的文稿保存成文件",
        "parameters": {"path": "string", "content": "string"}, "required": ["path", "content"],
        "risk": "medium", "available": True, "needs": "none",
        "examples": [{"path": "note.md", "content": "正文"}],
    },
}

# ---------------- 运行时状态（configure() 后生效） ----------------

_RUNTIME = {
    "groups": {},          # group -> enabled
    "capabilities": {},    # {"vision": bool, "audio": bool, "network": bool, "music_provider": bool}
    "limits": {},          # web 长度/超时等
    "configured": False,
}
_SCHEMA_CACHE: Dict[Tuple, str] = {}


def configure(config: dict = None, capabilities: dict = None, limits: dict = None) -> None:
    """应用配置与真实能力（由 runtime/service 在启动时调用一次）。"""
    config = config or {}
    tools_config = config.get("tools") or {}
    groups = {}
    for name, meta in GROUPS.items():
        section = tools_config.get(name)
        enabled = bool(section.get("enabled")) if isinstance(section, dict) \
            else bool(meta.get("default_enabled", False))
        groups[name] = enabled
    _RUNTIME["groups"] = groups
    _RUNTIME["capabilities"] = dict(capabilities or {})
    _RUNTIME["limits"] = dict(limits or {})
    _RUNTIME["configured"] = True
    _SCHEMA_CACHE.clear()


def group_enabled(group: str) -> bool:
    if not _RUNTIME["configured"]:
        return bool(GROUPS.get(group, {}).get("default_enabled", False))
    return bool(_RUNTIME["groups"].get(group, False))


def enabled_groups() -> List[str]:
    return [name for name in GROUPS if group_enabled(name)]


def tool_available(name: str) -> bool:
    """工具当前是否真的可用（配置 + 能力双重判断，绝不假装）。"""
    spec = TOOLS.get(name)
    if spec is None or not group_enabled(spec.get("group", "")):
        return False
    needs = spec.get("needs", "none")
    if needs in ("none", "network"):
        return True
    return bool(_RUNTIME["capabilities"].get(needs.replace("_provider", ""), False))


def _available_tools(groups: List[str]) -> List[str]:
    return [name for name, spec in TOOLS.items()
            if spec.get("group") in groups and tool_available(name)]


# ---------------- 兼容 API（v0.25.1 已有） ----------------

def get_tools() -> Dict:
    return TOOLS


def get_tool(name: str) -> Optional[Dict]:
    if not isinstance(name, str):
        return None
    return TOOLS.get(name.strip())


def tool_names() -> List[str]:
    return [name for name in TOOLS if tool_available(name)]


def all_tool_names() -> List[str]:
    return list(TOOLS.keys())


def parameter_names() -> List[str]:
    """所有「当前可用」工具参数名的并集（用于识别只写参数的坏调用）。"""
    names: List[str] = []
    for name in tool_names():
        for key in (TOOLS[name].get("parameters") or {}):
            if key not in names:
                names.append(key)
    return names


def list_tools() -> List[Dict]:
    """当前可用工具的条目（保持 v0.25.1 的字段 + 新增 group/risk）。"""
    entries = []
    for name in tool_names():
        spec = TOOLS[name]
        entries.append({"name": name, "group": spec.get("group", ""),
                        "description": spec.get("description", ""),
                        "parameters": dict(spec.get("parameters") or {}),
                        "required": list(spec.get("required") or []),
                        "risk": spec.get("risk", "low"),
                        "untrusted": bool(spec.get("untrusted", False))})
    return entries


def groups_summary() -> List[Dict]:
    """给 /tools 与 /health 用的分组概览。"""
    summary = []
    for name, meta in GROUPS.items():
        tools = [tool for tool, spec in TOOLS.items() if spec.get("group") == name]
        summary.append({"group": name, "description": meta.get("description", ""),
                        "enabled": group_enabled(name), "tools": tools,
                        "available": [tool for tool in tools if tool_available(tool)]})
    return summary


def describe_tools() -> str:
    """CLI /tools 输出：按组列出，并标出可用性。"""
    lines = ["当前工具（按组）："]
    for item in groups_summary():
        state = "on" if item["enabled"] else "off"
        lines.append("[%s] %s（%s）" % (item["group"], item["description"], state))
        for tool in item["tools"]:
            spec = TOOLS[tool]
            flag = "可用" if tool_available(tool) else "UNAVAILABLE"
            lines.append("  %-16s %-9s %s" % (tool, flag, spec.get("description", "")))
    return "\n".join(lines)


def describe_parameters(name: str) -> str:
    spec = get_tool(name) or {}
    parameters = spec.get("parameters") or {}
    if not parameters:
        return "无参数"
    required = set(spec.get("required") or [])
    return "、".join("%s（%s%s）" % (key, kind, "，必填" if key in required else "")
                     for key, kind in parameters.items())


def example_arguments(name: str) -> Dict:
    spec = get_tool(name) or {}
    examples = spec.get("examples") or [{}]
    first = examples[0]
    return dict(first) if isinstance(first, dict) else {}


# ---------------- 按需 Schema（带缓存） ----------------

def schema_text(groups: List[str] = None) -> str:
    """只包含指定组（默认：当前启用的组）的可用工具说明；带缓存。"""
    groups = list(groups) if groups is not None else enabled_groups()
    key = (tuple(sorted(groups)),) + tuple(sorted(_RUNTIME["capabilities"].items())) \
        + (tuple(sorted((k, str(v)) for k, v in _RUNTIME["groups"].items())),)
    cached = _SCHEMA_CACHE.get(key)
    if cached is not None:
        return cached
    lines = []
    for name in _available_tools(groups):
        spec = TOOLS[name]
        detail = describe_parameters(name)
        lines.append("- %s：%s，%s" % (name, spec.get("description", ""), detail))
    text = ""
    if lines:
        text = "可用工具（只有这些，不要编造工具名）：\n" + "\n".join(lines)
    _SCHEMA_CACHE[key] = text
    return text


def schema_cache_info() -> dict:
    return {"entries": len(_SCHEMA_CACHE), "keys": [len(key) for key in _SCHEMA_CACHE]}


# ---------------- 参数校验（沿用 v0.25.1 规则） ----------------

def _validate_file(arguments: Dict) -> Tuple[bool, str]:
    action = arguments.get("action")
    action = action.strip().lower() if isinstance(action, str) else ""
    if action not in ("read", "write"):
        return False, "参数 action 只能是 read 或 write，收到的是 %r" % (arguments.get("action"),)
    if action == "write" and not isinstance(arguments.get("content"), str):
        return False, "action 为 write 时必须提供字符串类型的 content 参数"
    return True, ""


def _validate_document(arguments: Dict) -> Tuple[bool, str]:
    if not isinstance(arguments.get("content"), str) or not arguments.get("content").strip():
        return False, "document_write 需要非空的 content 参数"
    return True, ""


_EXTRA_VALIDATORS = {"file": _validate_file, "document_write": _validate_document}


def validate_arguments(name: str, arguments: Dict) -> Tuple[bool, str]:
    """校验参数：工具存在、可用、参数类型、必填、未知参数（沿用 v0.25.1 行为）。"""
    spec = get_tool(name)
    if spec is None:
        return False, "没有名为 %s 的工具，可用工具：%s" % (name, ", ".join(tool_names()))
    if not group_enabled(spec.get("group", "")):
        return False, "工具 %s 所在分组 %s 已关闭（config.yaml tools.%s.enabled=false）" % (
            name, spec.get("group"), spec.get("group"))
    if not tool_available(name):
        return False, "工具 %s 当前不可用（需要 %s，实际 UNAVAILABLE）" % (name, spec.get("needs"))
    if not isinstance(arguments, dict):
        return False, "arguments 必须是 JSON 对象，例如 {\"command\": \"ls\"}"
    allowed = spec.get("parameters") or {}
    required = spec.get("required") or []
    missing = [key for key in required if key not in arguments]
    if missing:
        return False, "缺少必填参数：%s（该工具的参数：%s）" % (
            "、".join(missing), describe_parameters(name))
    unknown = [key for key in arguments if key not in allowed]
    if unknown:
        return False, "不支持的参数：%s（该工具的参数：%s）" % (
            "、".join(unknown), describe_parameters(name))
    for key, value in arguments.items():
        if not isinstance(value, str):
            return False, "参数 %s 必须是字符串，收到的是 %s" % (key, type(value).__name__)
    extra = _EXTRA_VALIDATORS.get(name)
    if extra is not None:
        return extra(arguments)
    return True, ""


# ---------------- 统一执行 ----------------

def _limits() -> Dict:
    return _RUNTIME["limits"]


def _do_time(arguments, context):
    from tools.core.time import get_current_time
    return get_current_time()


def _do_shell(arguments, context):
    from tools.system import shell
    return shell.run_command(str(arguments.get("command", "")),
                             timeout=int(context.get("tool_timeout", 15)))


def _do_file(arguments, context):
    from tools.system import file as file_tool
    action = str(arguments.get("action", "")).strip().lower()
    path = str(arguments.get("path", ""))
    if action == "write":
        return file_tool.write_file(path, str(arguments.get("content", "")))
    return file_tool.read_file(path)


def _do_weather(arguments, context):
    from tools.web import weather
    return weather.get_weather(arguments.get("location"), default_location=context.get("default_location"),
                               timeout=int(_limits().get("timeout", 10)))


def _do_web_search(arguments, context):
    from tools.web import search
    return search.web_search(str(arguments.get("query", "")),
                             limit=int(_limits().get("search_max_items", 5)),
                             timeout=int(_limits().get("timeout", 10)))


def _do_web_fetch(arguments, context):
    from tools.web import fetch
    return fetch.web_fetch(str(arguments.get("url", "")),
                           max_chars=int(_limits().get("fetch_max_chars", 2500)),
                           timeout=int(_limits().get("timeout", 10)))


def _do_image_info(arguments, context):
    from tools.vision import image
    return image.image_info(str(arguments.get("path_or_url", "")))


def _do_image_download(arguments, context):
    from tools.vision import image
    return image.image_download(str(arguments.get("url", "")), str(arguments.get("path", "")))


def _do_image_analyze(arguments, context):
    from tools.vision import image
    return image.image_analyze(arguments.get("path_or_url"), arguments.get("question"))


def _do_speech_to_text(arguments, context):
    from tools.audio import audio
    return audio.speech_to_text(arguments.get("path_or_url"))


def _do_text_to_speech(arguments, context):
    from tools.audio import audio
    return audio.text_to_speech(arguments.get("text"))


def _do_music_search(arguments, context):
    from tools.media import music
    return music.music_search(arguments.get("query"))


def _do_music_play(arguments, context):
    from tools.media import music
    return music.music_play(arguments.get("query"))


def _do_document_write(arguments, context):
    from tools.writing import writing
    return writing.document_write(str(arguments.get("path", "")), str(arguments.get("content", "")))


_DISPATCH = {
    "time": _do_time, "shell": _do_shell, "file": _do_file,
    "weather": _do_weather, "web_search": _do_web_search, "web_fetch": _do_web_fetch,
    "image_info": _do_image_info, "image_download": _do_image_download,
    "image_analyze": _do_image_analyze,
    "speech_to_text": _do_speech_to_text, "text_to_speech": _do_text_to_speech,
    "music_search": _do_music_search, "music_play": _do_music_play,
    "document_write": _do_document_write,
}


def invoke(name: str, arguments: Dict, context: Dict = None) -> Dict:
    """统一执行入口：Core 只调这里，不认识任何具体工具。返回原始工具结果。"""
    ok, error = validate_arguments(name, arguments)
    if not ok:
        return {"ok": False, "error": error}
    handler = _DISPATCH.get(name)
    if handler is None:
        return {"ok": False, "error": "工具 %s 没有实现" % name}
    try:
        raw = handler(arguments or {}, context or {})
    except Exception as exc:  # 工具内部异常也不许冒到 Core
        return {"ok": False, "error": "工具执行异常：%s" % str(exc)[:160]}
    if not isinstance(raw, dict):
        return {"ok": False, "error": "工具返回了无法识别的结果"}
    if raw.get("untrusted"):
        raw = dict(raw)
    return raw


# ---------------- 结果格式化（Core 不认识具体工具） ----------------

def _fmt_time(arguments, raw):
    return "当前时间：%s（%s，时区 %s）" % (raw.get("datetime"), raw.get("weekday"),
                                          raw.get("timezone"))


def _fmt_shell(arguments, raw):
    stdout = (raw.get("stdout") or "").strip()
    stderr = (raw.get("stderr") or "").strip()
    lines = ["命令：%s" % raw.get("command", arguments.get("command", "")),
             "输出：\n%s" % (stdout if stdout else "(无输出)")]
    if stderr:
        lines.append("错误输出：\n%s" % stderr)
    lines.append("退出码：%s" % raw.get("returncode"))
    return "\n".join(lines)


def _fmt_file(arguments, raw):
    if str(arguments.get("action", "")).strip().lower() == "write":
        return "已写入 %s（%s 字节）" % (raw.get("path"), raw.get("bytes"))
    suffix = "\n...(内容过长，已截断)" if raw.get("truncated") else ""
    return "文件 %s 的内容：\n%s%s" % (raw.get("path"), raw.get("content") or "", suffix)


def _fmt_weather(arguments, raw):
    return ("%s：%s，气温 %s（体感 %s），湿度 %s，风速 %s（%s，来源 %s）"
            % (raw.get("location"), raw.get("weather"), raw.get("temperature"),
               raw.get("feels_like"), raw.get("humidity"), raw.get("wind"),
               raw.get("observed_at", ""), raw.get("source", "")))


def _fmt_search(arguments, raw):
    lines = ["搜索结果（%d 条）：" % raw.get("count", 0)]
    for index, item in enumerate(raw.get("results") or [], 1):
        lines.append("%d. %s\n   %s\n   %s" % (index, item.get("title"),
                                               item.get("url"), item.get("snippet")))
    return "\n".join(lines)


def _fmt_fetch(arguments, raw):
    return "%s\n%s" % (raw.get("title") or raw.get("url"), raw.get("content") or "")


def _fmt_image(arguments, raw):
    if "path" in raw and "bytes" in raw:
        return "%s（%s 字节，%s）" % (raw.get("path"), raw.get("bytes"),
                                     raw.get("content_type", "") or "类型未知")
    return "%s（%s 字节，%s）" % (raw.get("url"), raw.get("bytes"),
                                 raw.get("content_type", "") or "类型未知")


def _fmt_document(arguments, raw):
    return "已保存文稿：%s（%s 字节）" % (raw.get("path"), raw.get("bytes"))


_FORMATTERS = {
    "time": _fmt_time, "shell": _fmt_shell, "file": _fmt_file,
    "weather": _fmt_weather, "web_search": _fmt_search, "web_fetch": _fmt_fetch,
    "image_info": _fmt_image, "image_download": _fmt_image, "image_prepare": _fmt_image,
    "document_write": _fmt_document,
}


def format_result(name: str, arguments: Dict, raw: Dict) -> Dict:
    """把原始工具结果统一成 {"ok": true, "result": "..."} / {"ok": false, "error": "..."}。"""
    if not isinstance(raw, dict):
        return {"ok": False, "error": "工具返回了无法识别的结果"}
    if not raw.get("ok"):
        return {"ok": False, "error": str(raw.get("error") or "工具执行失败")}
    formatter = _FORMATTERS.get(name)
    if formatter is None:
        text = str(raw.get("content") or raw.get("text") or raw.get("result") or raw)
        return {"ok": True, "result": text[:600]}
    return {"ok": True, "result": formatter(arguments or {}, raw)}


def is_untrusted(name: str) -> bool:
    """该工具的结果是否来自不可信外部内容（网页）。"""
    return bool((TOOLS.get(name) or {}).get("untrusted", False))


def availability_report() -> Dict:
    """给 /health 与报告用的可用性清单（AVAILABLE / UNAVAILABLE 如实标注）。"""
    tools = {}
    for name, spec in TOOLS.items():
        tools[name] = {
            "group": spec.get("group"), "available": tool_available(name),
            "reason": "" if tool_available(name) else "需要 %s 或分组未启用" % spec.get("needs"),
            "risk": spec.get("risk", "low"),
        }
    return {"groups": {name: group_enabled(name) for name in GROUPS}, "tools": tools,
            "schema_cache": schema_cache_info()}
