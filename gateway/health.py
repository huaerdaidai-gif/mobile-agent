# -*- coding: utf-8 -*-
"""Gateway 健康检查：把 Agent / Model / Gateway / QQ 四个状态汇总成一份可见结果。

Stage 3：model / host / qq 三项是彼此独立的 I/O 探测，用 Scheduler 并行执行
（不含任何模型推理并行）。
"""

import time

from runtime.scheduler import KIND_IO, Task


def collect(service, gateway_state: dict = None, model_probe: bool = True) -> dict:
    """汇总健康状态；任何子项失败都不会抛异常。"""
    started = time.time()
    components = {}

    # Agent Core：能建出 Agent、拿到上下文预算就算正常
    try:
        agent_ok = service.new_agent() is not None
        components["agent"] = {"ok": agent_ok, "context_limit": service.context_limit(),
                               "sessions": service.sessions.stats()["count"]}
    except Exception as exc:
        components["agent"] = {"ok": False, "error": str(exc)[:200]}

    # Model / Host / Capability 三块互不依赖 → 用调度器并行（纯 I/O）
    scheduler = getattr(service, "scheduler", None)
    probes = {
        "model": lambda: service.model_health(probe=model_probe),
        "host": service.host_info,
        "capability": service.capability_info,
    }
    if scheduler is not None:
        outcome = scheduler.run_io_parallel([Task(name, func, KIND_IO)
                                             for name, func in probes.items()])
        for name, value in outcome.results.items():
            components[name] = value if isinstance(value, dict) else {"ok": False, "error": str(value)}
        timings = outcome.timings
    else:
        for name, func in probes.items():
            try:
                components[name] = func()
            except Exception as exc:
                components[name] = {"ok": False, "error": str(exc)[:200]}
        timings = {}

    # Gateway 自身（由 server 填入监听地址）
    state = gateway_state if gateway_state is not None else service.gateway_state
    components["gateway"] = {"ok": bool(state.get("listening", state.get("ok", False))),
                             "host": state.get("host"), "port": state.get("port"),
                             "auth": state.get("auth"), "adapter": state.get("adapter", "http")}

    # QQ 适配器
    try:
        components["qq"] = service.qq_adapter.health()
    except Exception as exc:
        components["qq"] = {"ok": False, "error": str(exc)[:200]}

    ok = all(bool(item.get("ok")) for item in components.values())
    return {"ok": ok, "checked_at": time.time(),
            "elapsed_ms": int((time.time() - started) * 1000),
            "probe_ms": timings,
            "components": components}


def render_text(payload: dict) -> str:
    """把健康结果渲染成适合 CLI 看的文本。"""
    lines = ["总体: %s（%d ms）" % ("OK" if payload.get("ok") else "异常", payload.get("elapsed_ms", 0))]
    components = payload.get("components") or {}
    for name in ("agent", "model", "gateway", "qq", "host", "capability"):
        item = components.get(name)
        if not item:
            continue
        status = "OK" if item.get("ok") else "FAIL"
        detail = []
        for key in ("base_url", "host", "port", "context", "count", "bot_id", "error", "detail"):
            if item.get(key) not in (None, "", []):
                detail.append("%s=%s" % (key, item.get(key)))
        lines.append("  %-10s %-4s %s" % (name, status, " ".join(detail)))
    return "\n".join(lines)
