# -*- coding: utf-8 -*-
"""LLM 接口层。

通过 OpenAI Compatible API（POST {base_url}/chat/completions）连接本机的
llama.cpp server。不使用 OpenAI 官方 SDK，也不引入任何 Agent 框架。

HTTP 客户端优先使用 requests；如果运行环境（例如刚装好的 Termux）还没安装
requests，则自动回退到标准库 urllib，保证「零依赖也能跑起来」。

v0.2 新增流式输出：
  - 两条路径都支持 SSE 流式（requests 用 stream=True，urllib 用 urlopen 逐行读取）；
  - 标准库的 urlopen 返回对象同样是可迭代的，因此无需降级；
  - 若某个 server 忽略 stream=true 直接返回整段 JSON，这里也能正确解析出来。

v0.25.1 新增：
  - detect_server_context()：通过 GET /v1/models、GET /props 读取服务器真实上下文长度；
  - max_tokens 支持（对应配置 performance.max_output_tokens）；
  - 记录服务端返回的 timings / usage（last_timings、last_usage），只用于观测，不影响主流程。
"""

import json
from typing import Dict, List

# SSE 协议相关常量
SSE_DATA_PREFIX = "data:"
SSE_DONE = "[DONE]"

try:  # 优先使用 requests
    import requests

    _HAS_REQUESTS = True
except ImportError:  # 未安装 requests 时回退到标准库
    import urllib.error
    import urllib.request

    _HAS_REQUESTS = False


class LLMError(Exception):
    """LLM 调用失败（网络不通、超时、返回格式异常等）。"""


def http_client_name() -> str:
    """返回当前实际使用的 HTTP 客户端名称，用于启动时提示。"""
    return "requests" if _HAS_REQUESTS else "urllib（标准库回退）"


def _post_json(url: str, payload: Dict, timeout: int) -> Dict:
    """发送 POST 请求并返回解析后的 JSON，失败时抛出 LLMError。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    if _HAS_REQUESTS:
        try:
            resp = requests.post(url, data=body, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            raise LLMError("无法连接模型服务（%s）：%s" % (url, exc))
        if resp.status_code != 200:
            raise LLMError("模型服务返回 HTTP %s：%s" % (resp.status_code, resp.text[:200]))
        try:
            return resp.json()
        except ValueError:
            raise LLMError("模型服务返回的不是合法 JSON：%s" % resp.text[:200])

    # ---- 标准库回退实现 ----
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise LLMError("模型服务返回 HTTP %s：%s" % (exc.code, detail))
    except Exception as exc:  # 连接被拒绝、超时等
        raise LLMError("无法连接模型服务（%s）：%s" % (url, exc))
    try:
        return json.loads(raw)
    except ValueError:
        raise LLMError("模型服务返回的不是合法 JSON：%s" % raw[:200])


def _iter_sse_payloads(lines):
    """把 SSE 行流转成一个个 data 段字符串。

    兼容点：
      - 跳过空行与注释行（以 : 开头）；
      - 遇到 `data: [DONE]` 结束；
      - 少数 server 忽略 stream=true 直接返回整段 JSON，此时该行会被原样产出。
    """
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith(SSE_DATA_PREFIX):
            payload = line[len(SSE_DATA_PREFIX):].strip()
            if payload == SSE_DONE:
                return
            if payload:
                yield payload
            continue
        if line.startswith("{"):  # 非流式兜底：整段 JSON 响应
            if SSE_DONE in line:
                return
            yield line


def _load_object(payload: str):
    """解析一个 data 段，返回 dict（非法分片返回 None，服务端报错抛 LLMError）。"""
    try:
        obj = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("error"):
        raise LLMError("模型服务返回错误：%s" % str(obj["error"])[:200])
    return obj


def _delta_from(obj) -> str:
    """从已解析的对象里取出增量文本。"""
    if not isinstance(obj, dict):
        return ""
    choices = obj.get("choices") or []
    if not choices:
        return ""
    first = choices[0] or {}
    delta = first.get("delta")
    if isinstance(delta, dict) and delta.get("content"):
        return delta["content"]
    message = first.get("message")
    if isinstance(message, dict) and message.get("content"):
        return message["content"]
    return first.get("text") or ""


def _get_json(url: str, timeout: int):
    """GET 一个 JSON 接口，返回 (数据, 错误信息)；从不抛异常。"""
    headers = {"Accept": "application/json"}
    if _HAS_REQUESTS:
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            return None, str(exc)
        if resp.status_code != 200:
            return None, "HTTP %s" % resp.status_code
        try:
            return resp.json(), None
        except ValueError:
            return None, "返回内容不是 JSON"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return None, "HTTP %s" % exc.code
    except Exception as exc:
        return None, str(exc)
    try:
        return json.loads(raw), None
    except ValueError:
        return None, "返回内容不是 JSON"


def _pick_int(obj, paths):
    """按候选路径从嵌套字典里取一个正整数。"""
    for path in paths:
        value = obj
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.isdigit() and int(value) > 0:
            return int(value)
    return None


# /v1/models 里可能存放上下文长度的字段（n_ctx_train 是训练长度，故意不使用）
_MODELS_CONTEXT_PATHS = (
    ("meta", "n_ctx"),
    ("n_ctx",),
    ("context_length",),
    ("details", "context_length"),
    ("meta", "context_length"),
)

# /props 里可能存放上下文长度的字段
_PROPS_CONTEXT_PATHS = (
    ("default_generation_settings", "n_ctx"),
    ("n_ctx",),
)


def detect_server_context(base_url: str, timeout: int = 5) -> Dict:
    """探测模型服务真实可用的上下文长度。

    返回 {"context": int|None, "model": str|None, "source": str|None, "error": str|None}。

    依次尝试 GET /v1/models、GET /props；拿不到就返回 context=None，由调用方沿用配置值。
    刻意不使用 n_ctx_train：那是模型的训练上下文，不代表服务器实际开出来的长度。
    任何网络错误都不会抛出，只写入 error 字段。
    """
    info = {"context": None, "model": None, "source": None, "error": None}
    base = (base_url or "").rstrip("/")
    if not base:
        info["error"] = "未配置 base_url"
        return info

    data, error = _get_json(base + "/models", timeout)
    if isinstance(data, dict):
        items = data.get("data") or data.get("models") or []
        if isinstance(items, list) and items and isinstance(items[0], dict):
            first = items[0]
            info["model"] = first.get("id") or first.get("name") or first.get("model")
            info["context"] = _pick_int(first, _MODELS_CONTEXT_PATHS)
            if info["context"]:
                info["source"] = "GET /v1/models"
    else:
        info["error"] = error

    if info["context"] is None:
        root = base[:-3] if base.endswith("/v1") else base
        props, props_error = _get_json(root + "/props", timeout)
        if isinstance(props, dict):
            context = _pick_int(props, _PROPS_CONTEXT_PATHS)
            if context:
                info["context"] = context
                info["source"] = "GET /props"
            if not info["model"]:
                info["model"] = props.get("model_alias") or props.get("model_path")
        elif props_error and not info["error"]:
            info["error"] = props_error
    return info


def detect_server_capabilities(base_url: str, timeout: int = 5) -> Dict:
    """探测服务端真实能力（llama.cpp /props）。

    返回 {"context": int|None, "vision": bool|None, "audio": bool|None,
          "video": bool|None, "source": str|None, "error": str|None}

    vision/audio/video 为 None 表示「服务端没报这项能力」，调用方应按不可用处理，
    绝不假设支持。任何网络错误都不抛异常。
    """
    info = {"context": None, "vision": None, "audio": None, "video": None,
            "source": None, "error": None}
    base = (base_url or "").rstrip("/")
    if not base:
        info["error"] = "未配置 base_url"
        return info
    root = base[:-3] if base.endswith("/v1") else base
    data, error = _get_json(root + "/props", timeout)
    if not isinstance(data, dict):
        info["error"] = error or "无法读取 /props"
        return info
    modalities = data.get("modalities")
    if isinstance(modalities, dict):
        for key in ("vision", "audio", "video"):
            if key in modalities:
                info[key] = bool(modalities.get(key))
    info["context"] = _pick_int(data, _PROPS_CONTEXT_PATHS)
    info["source"] = "GET /props"
    return info


class LLM:
    """极简 Chat 客户端：把 messages 发给模型，取回文本。"""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080/v1",
        model: str = "qwen2.5-3b-instruct-q4_k_m.gguf",
        temperature: float = 0.2,
        timeout: int = 120,
        api_key: str = "local",
        max_tokens: int = 0,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.api_key = api_key  # 本地 llama.cpp 不校验，占位即可
        self.max_tokens = int(max_tokens or 0)  # 0 表示不限，交给服务端默认值
        # 服务端返回的性能信息（只用于观测，不参与对话逻辑）
        self.last_timings: Dict = {}
        self.last_usage: Dict = {}

    @property
    def endpoint(self) -> str:
        """完整的 chat/completions 地址。"""
        return self.base_url + "/chat/completions"

    def _payload(self, messages: List[Dict[str, str]], stream: bool,
                 response_format: Dict = None) -> Dict:
        """构造请求体。"""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": stream,
        }
        if self.max_tokens > 0:
            payload["max_tokens"] = self.max_tokens
        if response_format:
            # llama.cpp 支持 {"type": "json_object"}：用语法约束保证输出是合法 JSON，
            # 这对 1B~4B 小模型特别有用（v0.25.1 只在「重试」时使用）
            payload["response_format"] = response_format
        return payload

    def _record_meta(self, obj) -> None:
        """记录服务端返回的 timings / usage，用于性能观测。"""
        if not isinstance(obj, dict):
            return
        timings = obj.get("timings")
        if isinstance(timings, dict):
            self.last_timings = timings
        usage = obj.get("usage")
        if isinstance(usage, dict):
            self.last_usage = usage

    def chat(self, messages: List[Dict[str, str]], response_format: Dict = None) -> str:
        """发送对话消息，返回模型回复的纯文本。"""
        data = _post_json(self.endpoint,
                          self._payload(messages, stream=False, response_format=response_format),
                          self.timeout)
        self._record_meta(data)

        choices = data.get("choices") or []
        if not choices:
            raise LLMError("模型没有返回 choices 字段：%s" % str(data)[:200])

        first = choices[0] or {}
        message = first.get("message") or {}
        content = message.get("content")
        if content is None:  # 兼容极少数返回 text 字段的实现
            content = first.get("text", "")
        return (content or "").strip()

    def chat_stream(self, messages: List[Dict[str, str]], response_format: Dict = None):
        """以流式方式生成回答，逐段 yield 文本增量。

        调用方负责拼接；中途断开时会抛出 LLMError，已经 yield 出去的内容由调用方保留。
        """
        payload = self._payload(messages, stream=True, response_format=response_format)
        for data in self._stream_payloads(payload):
            obj = _load_object(data)
            self._record_meta(obj)
            delta = _delta_from(obj)
            if delta:
                yield delta

    def _stream_payloads(self, payload: Dict):
        """发起流式请求并产出 SSE 的 data 段（requests 与标准库两条实现）。"""
        url = self.endpoint
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}

        if _HAS_REQUESTS:
            try:
                resp = requests.post(url, data=body, headers=headers, timeout=self.timeout, stream=True)
            except requests.RequestException as exc:
                raise LLMError("无法连接模型服务（%s）：%s" % (url, exc))
            with resp:  # 确保连接一定被释放
                if resp.status_code != 200:
                    raise LLMError("模型服务返回 HTTP %s：%s" % (resp.status_code, resp.text[:200]))
                try:
                    for data in _iter_sse_payloads(resp.iter_lines(decode_unicode=False)):
                        yield data
                except LLMError:
                    raise
                except requests.RequestException as exc:  # 中途断流
                    raise LLMError("流式响应中断：%s" % exc)
                except Exception as exc:
                    raise LLMError("流式响应读取失败：%s" % exc)
            return

        # ---- 标准库回退：urlopen 的返回对象可迭代，同样可以逐行流式读取 ----
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise LLMError("模型服务返回 HTTP %s：%s" % (exc.code, detail))
        except Exception as exc:
            raise LLMError("无法连接模型服务（%s）：%s" % (url, exc))
        with resp:
            try:
                for data in _iter_sse_payloads(resp):
                    yield data
            except LLMError:
                raise
            except Exception as exc:  # 超时、连接被重置、响应不完整等
                raise LLMError("流式响应中断：%s" % exc)
