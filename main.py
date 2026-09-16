#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mobile Agent v0.25 —— CLI 入口。

用法：
    python main.py

启动后会读取同目录下的 config.yaml，连接本机 llama.cpp 的 OpenAI 兼容接口。
输入 exit / quit / 退出 结束会话；
/status 查看运行状态，/clear 清空上下文，/tools 查看工具清单。
"""

import os
import sys

from agent import MEMORY_PATH, Agent
from llm import LLM, LLMError, detect_server_context, http_client_name

try:  # Termux 检测是可选能力，导入失败也不影响运行
    from platform import has_termux_api, is_termux
except ImportError:  # pragma: no cover - 仅在包被裁剪时触发
    def is_termux() -> bool:
        return False

    def has_termux_api() -> bool:
        return False

# 版本号（启动横幅与 /status 共用）
VERSION = "0.27"

# 项目根目录（以本文件所在目录为准，任何 cwd 下都能正确加载配置）
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.yaml")

# 默认配置：config.yaml 缺失或字段不全时的兜底
DEFAULT_CONFIG = {
    # 顶层 stream 与 llm.stream 都支持，这里给默认值
    "stream": True,
    "llm": {
        "base_url": "http://127.0.0.1:8080/v1",
        "model": "qwen2.5-3b-instruct-q4_k_m.gguf",
        "temperature": 0.2,
        "timeout": 300,
    },
    "agent": {
        "max_context": 4096,
        "max_steps": 4,
        "max_memory_entries": 50,
    },
    "memory": {
        "enabled": True,
        "max_items": 10,
    },
    "tools": {
        "enabled": True,
        # v0.25：默认只接受统一协议；设为 true 时额外兼容 v0.2 的旧协议
        "legacy_protocol": False,
        # v0.25.1：小模型把 JSON 标点写成全角时，自动按半角解析
        "normalize_punctuation": True,
        # v0.25.1：接受「参数写在顶层」的等价写法
        "flat_arguments": True,
    },
    # v0.25.1 性能相关（速度优先）
    "performance": {
        "auto_detect_context": True,
        "tool_result_max_chars": 4000,
        "memory_max_items": 5,
        "memory_max_ratio": 0.10,
        "system_prompt_max_tokens": 300,
        "max_output_tokens": 512,
        "chat_tool_gate": True,
        # Stage 2/3：Web 工具的限制（网页内容必须硬限制）
        "web_result_max_chars": 1500,
        "web_fetch_max_chars": 2500,
        "web_search_max_items": 5,
        "web_timeout": 10,
    },
}


def _cast(value: str):
    """把 YAML 里的字符串转换成 int / float / bool / str。"""
    value = value.strip().strip('"').strip("'")
    lowered = value.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_simple_yaml(text: str) -> dict:
    """极简 YAML 解析：支持任意层级的「键: 值」映射（本项目配置只用这一种子集）。

    支持：嵌套映射（按缩进）、标量、行尾 # 注释。
    不支持：列表、锚点、多行字符串（配置文件里没有用到）。
    这样未安装 PyYAML 的 Termux 也能解析 models.primary.enabled 这种三层结构。
    """
    root = {}
    stack = [(-1, root)]  # (缩进, 对应的 dict)
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        key, sep, value = line.strip().partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if not value:  # 形如 "models:"，开启一个子映射
            node = {}
            parent[key] = node
            stack.append((indent, node))
        else:
            parent[key] = _cast(value)
    return root


def load_config(path: str = CONFIG_PATH) -> dict:
    """读取 config.yaml，并与默认配置合并；任何异常都退回默认值。"""
    config = {key: (dict(value) if isinstance(value, dict) else value)
              for key, value in DEFAULT_CONFIG.items()}
    if not os.path.exists(path):
        print("[提示] 未找到 config.yaml，使用内置默认配置。")
        return config

    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        print("[提示] 读取 config.yaml 失败（%s），使用默认配置。" % exc)
        return config

    try:
        import yaml  # 可选依赖

        loaded = yaml.safe_load(text) or {}
    except ImportError:
        loaded = _parse_simple_yaml(text)
    except Exception as exc:  # YAML 语法错误
        print("[提示] config.yaml 解析失败（%s），使用默认配置。" % exc)
        return config

    for section, values in (loaded or {}).items():
        if isinstance(values, dict) and isinstance(config.get(section), dict):
            config[section].update(values)
        else:
            config[section] = values
    return config


def build_agent(config: dict, llm=None, server_info: dict = None, verbose: bool = True,
                extra_agent_kwargs: dict = None) -> Agent:
    """根据配置组装 LLM 与 Agent（含 v0.25.1 的上下文自动探测）。

    llm / server_info 可选：Gateway 会复用同一个 LLM 实例与已探测到的上下文，
    避免每个会话都重新探测一次；不传时行为与 v0.25.1 完全一致。
    extra_agent_kwargs：Gateway 注入模型路由器 / 默认城市等（保持向后兼容）。
    """
    llm_config = config["llm"]
    agent_config = config["agent"]
    memory_config = config.get("memory") or {}
    tools_config = config.get("tools") or {}
    perf = config.get("performance") or {}

    max_tokens = int(perf.get("max_output_tokens", 512) or 0)

    # 1. 探测服务器真实上下文（GET /v1/models → GET /props）
    if server_info is None:
        server_info = {"context": None, "model": None, "source": None, "error": None}
        if bool(perf.get("auto_detect_context", True)):
            server_info = detect_server_context(llm_config["base_url"], timeout=5)

    if llm is None:
        llm = LLM(
            base_url=llm_config["base_url"],
            model=llm_config["model"],
            temperature=float(llm_config["temperature"]),
            timeout=int(llm_config["timeout"]),
            max_tokens=max_tokens,
        )

    agent = Agent(
        llm=llm,
        max_context=int(agent_config["max_context"]),
        max_steps=int(agent_config["max_steps"]),
        max_memory_entries=int(agent_config["max_memory_entries"]),
        # 记忆文件路径：配置了 memory.path 就用它（多实例 / 测试隔离），否则用默认位置
        memory_path=(memory_config.get("path") or MEMORY_PATH),
        stream=stream_enabled(config),
        memory_enabled=bool(memory_config.get("enabled", True)),
        memory_max_items=int(perf.get("memory_max_items", memory_config.get("max_items", 5))),
        tools_enabled=bool(tools_config.get("enabled", True)),
        legacy_protocol=bool(tools_config.get("legacy_protocol", False)),
        normalize_punctuation=bool(tools_config.get("normalize_punctuation", True)),
        flat_arguments=bool(tools_config.get("flat_arguments", True)),
        chat_tool_gate=bool(perf.get("chat_tool_gate", True)),
        server_context=server_info.get("context"),
        tool_result_max_chars=int(perf.get("tool_result_max_chars", 4000)),
        memory_max_ratio=float(perf.get("memory_max_ratio", 0.10)),
        system_prompt_max_tokens=int(perf.get("system_prompt_max_tokens", 300)),
        verbose=verbose,
        **(extra_agent_kwargs or {})
    )
    # 把探测信息挂到 agent 上，/status 与启动横幅直接用
    agent.server_info = server_info
    return agent


def stream_enabled(config: dict) -> bool:
    """是否开启流式输出。

    v0.2 推荐写在顶层（stream: true）；为兼容旧配置，llm.stream 也可以。
    """
    if "stream" in config:
        return bool(config["stream"])
    return bool((config.get("llm") or {}).get("stream", True))


def friendly_error(exc: Exception) -> str:
    """把异常转换成简洁的人类可读信息（绝不输出 traceback）。"""
    text = str(exc) or exc.__class__.__name__
    if "无法连接模型服务" in text:
        return text + "\n       提示：请确认 llama.cpp server 已启动（llama-server -m 模型 --port 8080）。"
    return text


def print_banner(config: dict) -> None:
    """打印启动信息。"""
    print("Mobile Agent v%s" % VERSION)
    print("-" * 36)
    print("模型接口: %s" % config["llm"]["base_url"])
    print("模型名称: %s" % config["llm"]["model"])
    print("HTTP 客户端: %s（流式输出: %s）" % (http_client_name(), "开" if stream_enabled(config) else "关"))
    print("输入 exit 退出，/status 查看状态，/clear 清空上下文，/tools 查看工具")


def print_status(config: dict, agent: Agent) -> None:
    """打印 /status 运行状态（不显示任何密钥）。"""
    memory = agent.memory_summary()
    context = agent.context_summary()
    perf = config.get("performance") or {}
    server = (config["llm"]["base_url"] or "").replace("http://", "").replace("https://", "").rstrip("/")
    if server.endswith("/v1"):
        server = server[:-3]

    memory_state = "on" if memory["enabled"] else "off"
    if memory["enabled"]:
        memory_state += "（%d条）" % memory["injected"]
    if memory["corrupted"]:
        memory_state += " [记忆文件损坏，已忽略]"

    print("Mobile Agent v%s" % VERSION)
    print("模型：")
    print(config["llm"]["model"])
    print("服务器：")
    print(server)
    print("上下文：")
    print("配置 %d" % context["configured"])
    if context["server"]:
        print("服务器 %d" % context["server"])
    else:
        print("服务器 未知（%s）" % (context["source"] if context["source"] == "server" else "探测失败，沿用配置值"))
    print("实际 %d%s" % (context["effective"], "（服务器限制）" if context["source"] == "server" else ""))
    print("Stream：%s" % ("on" if stream_enabled(config) else "off"))
    print("Memory：%s" % memory_state)
    print("Tools：%s" % ("on" if agent.tools_enabled else "off"))
    print("Tool result limit：%d chars" % agent.tool_result_max_chars)
    print("输出上限：%d tokens" % int(perf.get("max_output_tokens", 512)))
    print("system prompt：约 %d tokens（上限 %s）"
          % (context["system_prompt_tokens"], perf.get("system_prompt_max_tokens", 300)))
    print("Termux：%s%s" % ("yes" if is_termux() else "no",
                            "（termux-api 可用）" if has_termux_api() else "（termux-api 不可用）"))
    print("HTTP 客户端：%s" % http_client_name())


def main() -> int:
    """CLI 主循环。"""
    config = load_config()

    try:
        agent = build_agent(config)
    except (KeyError, ValueError) as exc:
        print("[错误] 配置有误：%s" % exc)
        return 1

    print_banner(config)
    context = agent.context_summary()
    print("上下文预算: %d%s" % (context["effective"],
                                "（服务器限制）" if context["source"] == "server" else "（配置值）"))
    print("system prompt: 约 %d tokens" % context["system_prompt_tokens"])
    if (agent.server_info or {}).get("error") and context["server"] is None:
        print("[提示] 未能探测服务器上下文（%s），沿用配置值 %d"
              % ((agent.server_info or {}).get("error"), context["configured"]))

    while True:
        try:
            user_input = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):  # Ctrl+D / Ctrl+C 正常退出
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "退出"):
            break
        if user_input in ("/clear", "/reset"):
            agent.reset()
            print("[已清空本次会话上下文]")
            continue
        if user_input == "/tools":
            print(Agent.tool_list())
            if not agent.tools_enabled:
                print("（注意：config.yaml 里 tools.enabled = false，工具已被禁用）")
            continue
        if user_input == "/status":
            print_status(config, agent)
            continue

        # 回答边生成边显示：第一次收到文本时才打印 "Agent> " 前缀，
        # 这样工具调用的状态行不会和回答挤在同一行。
        shown = {"prefix": False}

        def emit(text: str) -> None:
            if not shown["prefix"]:
                print("Agent> ", end="")
                shown["prefix"] = True
            print(text, end="", flush=True)

        try:
            agent.ask(user_input, on_text=emit)
        except LLMError as exc:
            print("[错误] %s" % friendly_error(exc))
            continue
        except KeyboardInterrupt:  # Ctrl+C 只中断这一次生成
            print("\n[已中断本次生成]")
            continue
        except Exception as exc:  # 兜底：普通用户不应该看到 traceback
            print("[错误] 运行时异常：%s" % friendly_error(exc))
            continue
        print()

    print("再见。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已退出。")
        sys.exit(130)
    except Exception as exc:  # 最后一道防线：不向普通用户抛 traceback
        if os.environ.get("MOBILE_AGENT_DEBUG"):  # 需要排查时再打开
            raise
        print("[错误] 程序异常退出：%s" % friendly_error(exc))
        sys.exit(1)
