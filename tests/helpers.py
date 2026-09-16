# -*- coding: utf-8 -*-
"""测试公共工具：造配置、起服务。"""

import copy
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def make_config(base_url: str, tmpdir: str, **overrides) -> dict:
    """造一份最小可用配置（不读磁盘上的 config.yaml）。"""
    config = {
        "stream": True,
        "llm": {"base_url": base_url, "model": "mock-model", "temperature": 0.2, "timeout": 30},
        "agent": {"max_context": 2048, "max_steps": 4, "max_memory_entries": 50},
        # path 指向临时目录：测试绝不写进项目真实记忆
        "memory": {"enabled": True, "max_items": 10, "path": os.path.join(tmpdir, "memory.json")},
        "tools": {"enabled": True, "legacy_protocol": False, "normalize_punctuation": True,
                  "flat_arguments": True},
        "performance": {"auto_detect_context": True, "tool_result_max_chars": 4000,
                        "memory_max_items": 5, "memory_max_ratio": 0.10,
                        "system_prompt_max_tokens": 300, "max_output_tokens": 256,
                        "chat_tool_gate": True},
        "model": {"provider": "openai-compatible", "base_url": "", "name": ""},
        "gateway": {"host": "127.0.0.1", "port": 0, "auth": "none", "token": "",
                    "max_sessions": 4, "idle_seconds": 600},
        "qq": {"enabled": False, "adapter": "onebot", "api_base": "http://127.0.0.1:1",
               "token": "", "self_id": "", "require_at": True, "reply_group": True,
               "at_sender": False, "max_chars": 1200, "allow_users": ""},
        "host": {"profile": "generic"},
        "extension": {"admin_enabled": False},
        "logging": {"level": "ERROR", "file": ""},
    }
    for section, values in overrides.items():
        if isinstance(values, dict) and isinstance(config.get(section), dict):
            config[section].update(values)
        else:
            config[section] = values
    # 每个测试用独立的记忆文件，互不干扰
    memory_path = os.path.join(tmpdir, "memory.json")
    config["_memory_path"] = memory_path
    return copy.deepcopy(config)


def build_agent_for_test(config: dict, provider, **kwargs):
    """用测试配置建一个 Agent（复用 main.build_agent，但指定记忆文件）。"""
    import main as core_cli
    agent = core_cli.build_agent(config, llm=provider.llm if provider else None,
                                 server_info={"context": 2048}, verbose=False)
    if config.get("_memory_path"):
        agent.memory_path = config["_memory_path"]
        agent.memory = agent._load_memory()
        agent.memory_block, agent.memory_items = agent._build_memory_block()
    for key, value in kwargs.items():
        setattr(agent, key, value)
    return agent
