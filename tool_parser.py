# -*- coding: utf-8 -*-
"""工具调用解析器（v0.25 新增）。

新协议（唯一被接受的格式）：

    {"type": "tool_call", "name": "shell", "arguments": {"command": "ls"}}

支持四种输入形态：
  1. 纯 JSON
  2. ```json 代码块（``` 或 ```json / ```JSON 都行）
  3. JSON 前后存在少量解释文字
  4. 非法 JSON 返回 None

严格规则（不猜字段）：
  - 顶层必须是 JSON 对象；
  - type 必须是字符串 "tool_call"（忽略大小写与首尾空格）；
  - name 必须存在，且是非空字符串；
  - arguments 必须存在，且是 JSON 对象（dict）。
以上任何一条不满足都返回 None，由 Agent 当作普通回答处理，不去猜模型想调用什么。
"""

import json
from typing import Dict, List, Optional

# 协议里固定的类型标记
TOOL_CALL_TYPE = "tool_call"

# 小模型（尤其 Qwen2.5-3B 在中文语境下）经常把 JSON 里的半角标点写成全角，
# 例如 {"type":"tool_call","name":"time","arguments：{}}
# 这里做一次「标点归一化」：只替换标点，不改动字段名和结构，绝不猜测字段。
_PUNCTUATION_MAP = {
    "\uff1a": ":",   # ：
    "\uff0c": ",",   # ，
    "\uff02": '"',   # ＂
    "\u201c": '"',   # “
    "\u201d": '"',   # ”
    "\uff5b": "{",   # ｛
    "\uff5d": "}",   # ｝
    "\uff3b": "[",   # ［
    "\uff3d": "]",   # ］
}


def normalize_punctuation(text: str) -> str:
    """把 JSON 里常见的全角标点替换成半角，只改标点、不改内容。

    这是一层显式的容错（可在 config.yaml 里用 tools.normalize_punctuation 关闭），
    不是字段猜测：字段名、结构、取值都保持模型原本输出的样子。
    """
    if not text:
        return text
    for full_width, half_width in _PUNCTUATION_MAP.items():
        text = text.replace(full_width, half_width)
    return text


def extract_json_objects(text: str) -> List[str]:
    """从文本里提取所有「大括号配对平衡」的 JSON 片段。

    扫描时会跳过 JSON 字符串内部的大括号，因此 {"a": "}{"} 也能正确识别。
    这是纯文本层面的提取，不保证每个片段都是合法 JSON。
    """
    blocks: List[str] = []
    if not text:
        return blocks
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = index
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    blocks.append(text[start:index + 1])
                    start = -1
    return blocks


def _candidate_texts(text: str) -> List[str]:
    """返回待扫描的候选文本：代码块内容 + 原文本身。"""
    candidates: List[str] = []
    if "```" in text:  # 兼容小模型爱加代码块的习惯（```json 或 ```）
        for chunk in text.split("```")[1::2]:
            body = chunk.strip()
            if body[:4].lower() == "json":
                body = body[4:].lstrip()
            candidates.append(body)
    candidates.append(text.strip())
    return candidates


def _validate(obj, allow_flat: bool = False) -> Optional[Dict]:
    """严格校验一条工具调用，通过则返回规范化的字典，否则 None。

    allow_flat=True 时接受「参数写在顶层」的写法（Qwen2.5-3B 有一半概率这么写）：
        {"type":"tool_call","name":"file","action":"read","path":"."}
    等价于 arguments={"action":"read","path":"."}。参数名仍然要经过工具注册表校验，
    所以这不会执行到「猜出来的」调用。
    """
    if not isinstance(obj, dict):
        return None
    call_type = obj.get("type")
    if not isinstance(call_type, str) or call_type.strip().lower() != TOOL_CALL_TYPE:
        return None
    name = obj.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    arguments = obj.get("arguments")
    if not isinstance(arguments, dict) and allow_flat:
        extra = {key: value for key, value in obj.items()
                 if key not in ("type", "name", "arguments")}
        arguments = extra  # 没有额外键时是 {}，交给注册表判断必填参数
    if not isinstance(arguments, dict):
        return None
    # 只取协议约定的三个字段，其余字段不猜测、不传递
    return {"type": TOOL_CALL_TYPE, "name": name.strip(), "arguments": arguments}


def parse_tool_call(text: str, normalize: bool = False, allow_flat: bool = False) -> Optional[Dict]:
    """解析模型输出里的工具调用。

    返回 {"type": "tool_call", "name": str, "arguments": dict}，解析失败返回 None。
    normalize=True 时，会先把全角标点替换成半角再解析（见 normalize_punctuation）。
    allow_flat=True 时，接受参数直接写在顶层的写法（见 _validate）。
    """
    if not text or not isinstance(text, str):
        return None
    source = normalize_punctuation(text) if normalize else text
    for candidate in _candidate_texts(source):
        for block in extract_json_objects(candidate):
            try:
                obj = json.loads(block)
            except ValueError:
                continue  # 非法 JSON 直接跳过，不尝试修补
            call = _validate(obj, allow_flat=allow_flat)
            if call is not None:
                return call
    return None


def looks_like_tool_attempt(text: str, known_params=None) -> bool:
    """判断一段输出像不像「想调用工具，但 JSON 不合法 / 字段不合规」。

    刻意做得非常保守（v0.25.1）：普通回答里出现 `{`、`tool`、`JSON`、`工具` 这些词
    都不算数，必须真的出现一个 JSON 对象，并且带有明确的协议痕迹：

      1. 出现 `tool_call`；或
      2. 同时出现 type / name / arguments 里的两个以上关键字，并且带 tool 字样；或
      3. 这个 JSON 对象的键全部是「已知工具的参数名」（说明模型只写了 arguments，
         漏掉了 type / name 外壳）。known_params 由调用方从工具注册表传入。

    这样「你好，请介绍一下你自己」这类普通回答不会被误判，也就不会触发无谓的重试。
    本函数只用于决定要不要给一次友好提醒，不参与真正的工具调用判断。
    """
    if not text:
        return False
    lowered = text.lower()
    if "{" not in lowered:
        return False
    # 明确写出 tool_call 这个协议标记，即使 JSON 被截断/写错，也算「想调用工具」
    # （真实联调里 Qwen2.5-3B 会输出 {"type":"tool_call","name":"time","arguments：{} 这种残缺 JSON）
    if "tool_call" in lowered:
        return True
    blocks = extract_json_objects(text)
    if not blocks:
        return False  # 没有成对的 JSON 对象，按普通回答处理
    markers = sum(1 for key in ('"type"', '"name"', '"arguments"') if key in lowered)
    if markers >= 2 and ("tool" in lowered or "工具" in text):
        return True
    # 情况 3：只写了参数（例如 {"action":"file","path":"."}），漏掉协议外壳
    if known_params:
        params = set(known_params)
        for block in blocks:
            try:
                obj = json.loads(block)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj and set(obj.keys()) <= params:
                return True
    return False


def parse_legacy_tool_call(text: str) -> Optional[Dict]:
    """解析 v0.2 的旧协议（默认关闭，仅作为过渡兼容）。

    旧格式：{"tool": "shell", "command": "ls"}
    转换后：{"type": "tool_call", "name": "shell", "arguments": {"command": "ls"}}

    只有在 config.yaml 里把 tools.legacy_protocol 设为 true 时才会用到。
    """
    if not text or not isinstance(text, str):
        return None
    for candidate in _candidate_texts(text):
        for block in extract_json_objects(candidate):
            try:
                obj = json.loads(block)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            name = obj.get("tool")
            if not isinstance(name, str) or name.strip().lower() in ("", "none", "null"):
                continue
            arguments = {key: value for key, value in obj.items() if key != "tool"}
            return {"type": TOOL_CALL_TYPE, "name": name.strip(), "arguments": arguments,
                    "legacy": True}
    return None
