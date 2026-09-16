# -*- coding: utf-8 -*-
"""Gateway 健康检查：把 Agent / Model / Gateway / QQ 四个状态汇总成一份可见结果。"""

import time


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

    # Model
    try:
        components["model"] = service.model_health(probe=model_probe)
    except Exception as exc:
        components["model"] = {"ok": False, "error": str(exc)[:200]}

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

    # Host
    try:
        components["host"] = service.host_info()
    except Exception as exc:
        components["host"] = {"ok": False, "error": str(exc)[:200]}

    # Capability
    try:
        components["capability"] = service.capability_info()
    except Exception as exc:
        components["capability"] = {"ok": False, "error": str(exc)[:200]}

    ok = all(bool(item.get("ok")) for item in components.values())
    return {"ok": ok, "checked_at": time.time(),
            "elapsed_ms": int((time.time() - started) * 1000),
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
