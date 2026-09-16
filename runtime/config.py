# -*- coding: utf-8 -*-
"""配置加载：在 v0.25.1 的 config.yaml 之上补充 runtime/gateway/qq/host 等段。

设计要点：
  1. 完全复用 main.load_config()（含内置极简 YAML 解析），不重写配置解析；
  2. 只做「补默认值 + 环境变量展开 + 取值辅助」，不改动已有键；
  3. 新增段一律保持两层结构，未装 PyYAML 的 Termux 也能解析；
  4. 敏感信息用 ${ENV_VAR} 引用，不写死在源码里。
"""

import os
import re

import main as core_cli

# 新增段的默认值（不含敏感信息）
DEFAULT_SECTIONS = {
    "model": {
        "provider": "openai-compatible",
        "base_url": "",          # 留空则用 llm.base_url
        "name": "",              # 留空则用 llm.model
    },
    "gateway": {
        "host": "127.0.0.1",
        "port": 8787,
        "auth": "none",          # none | token
        "token": "",
        "max_sessions": 16,
        "idle_seconds": 3600,
    },
    "qq": {
        "enabled": False,
        "adapter": "onebot",
        "api_base": "http://127.0.0.1:3000",
        "token": "",
        # 事件推送（OneBot -> 网关）时要求的 token；留空表示不校验（仅监听 127.0.0.1 时）
        "push_token": "",
        "self_id": "",
        "reply_group": True,
        "at_sender": False,
        "max_chars": 1200,
        "allow_users": "",       # 逗号分隔的白名单，空表示不限制
    },
    "host": {
        "profile": "auto",       # auto | termux | generic
    },
    # 工具组开关（v0.25.1 的标量键保持兼容；新增按组开关）
    "tools": {
        "enabled": True,
        "legacy_protocol": False,
        "normalize_punctuation": True,
        "flat_arguments": True,
        "core": {"enabled": True},      # time
        "system": {"enabled": True},    # file / shell
        "web": {"enabled": True},       # weather / web_search / web_fetch
        "writing": {"enabled": True},   # document_write
        "vision": {"enabled": False},   # 未部署 → unavailable
        "audio": {"enabled": False},    # 未部署 → unavailable
        "music": {"enabled": False},    # 无可靠 Provider → unavailable
    },
    # Stage 1：多模型角色（primary 常驻；vision/audio 默认不可用，需要时再启用）
    "models": {
        "primary": {"enabled": True},
        "vision": {"enabled": False, "name": "", "endpoint": "",
                   "timeout": 120, "max_output_tokens": 256},
        "audio": {"enabled": False, "name": "", "endpoint": "",
                  "timeout": 120, "max_output_tokens": 256},
    },
    # Stage 3：轻量调度（当前单 slot，模型并行默认关闭）
    "scheduler": {
        "max_parallel_tasks": 2,
        "max_active_models": 1,
        "parallel_models": False,
        "model_timeout": 300,
        "tool_timeout": 15,
    },
    "extension": {
        "admin_enabled": False,  # 高风险接口默认关闭
    },
    "logging": {
        "level": "INFO",
        "file": "",              # 留空表示只输出到 stdout
    },
}

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(value):
    """把 ${VAR} 或 ${VAR:-默认值} 展开成环境变量内容。"""
    if isinstance(value, str):
        def replace(match):
            name, default = match.group(1), match.group(2)
            return os.environ.get(name, default if default is not None else "")
        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    return value


def load(path: str = None, overrides: dict = None) -> dict:
    """读取配置：core 配置 + 新增段默认值 + 环境变量展开 + 显式覆盖。"""
    config = core_cli.load_config(path) if path else core_cli.load_config()
    for section, defaults in DEFAULT_SECTIONS.items():
        current = config.get(section)
        merged = dict(defaults)
        if isinstance(current, dict):
            merged.update(current)
        # 对 models.<role> 这类「两层字典」再多合并一层默认值，避免丢默认键
        for key, default_value in defaults.items():
            if isinstance(default_value, dict):
                sub = current.get(key) if isinstance(current, dict) else None
                if isinstance(sub, dict):
                    merged[key] = dict(default_value, **sub)
        config[section] = merged
    config = _expand_env(config)
    for section, values in (overrides or {}).items():
        if isinstance(values, dict) and isinstance(config.get(section), dict):
            config[section].update(values)
        else:
            config[section] = values
    return config


def get(config: dict, path: str, default=None):
    """按 "gateway.port" 这样的路径取值。"""
    node = config
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return default if node is None else node


def model_settings(config: dict) -> dict:
    """模型相关设置：model.* 优先，缺省回落到 llm.*（v0.25.1 的配置）。"""
    llm = config.get("llm") or {}
    perf = config.get("performance") or {}
    return {
        "provider": get(config, "model.provider", "openai-compatible"),
        "base_url": get(config, "model.base_url", "") or llm.get("base_url", ""),
        "name": get(config, "model.name", "") or llm.get("model", ""),
        "temperature": float(llm.get("temperature", 0.2)),
        "timeout": int(llm.get("timeout", 300)),
        "max_output_tokens": int(perf.get("max_output_tokens", 512) or 0),
    }


def context_limit(config: dict) -> int:
    """上下文上限（配置值，真正的预算还会与服务器取较小值）。"""
    return int((config.get("agent") or {}).get("max_context", 4096))


def tool_limit(config: dict) -> int:
    """单条工具结果字符上限。"""
    return int((config.get("performance") or {}).get("tool_result_max_chars", 4000))


def web_limits(config: dict) -> dict:
    """Web 工具的长度/超时限制（网页内容永远是不可信数据，必须硬限制）。"""
    perf = config.get("performance") or {}
    return {
        "result_max_chars": int(perf.get("web_result_max_chars", 1500) or 1500),
        "fetch_max_chars": int(perf.get("web_fetch_max_chars", 2500) or 2500),
        "search_max_items": int(perf.get("web_search_max_items", 5) or 5),
        "weather_max_chars": int(perf.get("weather_max_chars", 1000) or 1000),
        "timeout": int(perf.get("web_timeout", 10) or 10),
    }


def group_enabled(config: dict, group: str, default: bool = True) -> bool:
    """某个工具组是否启用（缺省按 default）。"""
    section = (config.get("tools") or {}).get(group)
    if isinstance(section, dict):
        return bool(section.get("enabled", default))
    return default


def warnings(config: dict):
    """配置自检：返回人类可读的告警列表（不抛异常）。"""
    issues = []
    if get(config, "qq.enabled") and not get(config, "qq.token"):
        issues.append("qq.enabled=true 但没有配置 qq.token（OneBot 的 access_token）")
    if get(config, "gateway.auth") == "token" and not get(config, "gateway.token"):
        issues.append("gateway.auth=token 但没有配置 gateway.token")
    if get(config, "extension.admin_enabled"):
        issues.append("extension.admin_enabled=true：管理接口已开启，请确保有访问控制")
    if not model_settings(config)["base_url"]:
        issues.append("未配置模型地址（model.base_url 或 llm.base_url）")
    return issues
