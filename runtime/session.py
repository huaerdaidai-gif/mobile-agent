# -*- coding: utf-8 -*-
"""会话管理：session_id → Agent 实例，带条数上限与空闲回收。

并发说明：Agent 的 history 不是线程安全的，所以每个会话自带一把锁；
同一会话串行处理，不同会话可以并行（模型服务端自己排队）。
"""

import threading
import time
from typing import Callable, Dict, Optional

from runtime import logging as agent_log


class Session(object):
    """一个会话：一个 Agent + 一把锁 + 使用记录。"""

    def __init__(self, session_id: str, agent):
        self.id = session_id
        self.agent = agent
        self.lock = threading.Lock()
        self.created_at = time.time()
        self.last_used = self.created_at
        self.turns = 0

    def ask(self, text: str, on_text: Optional[Callable[[str], None]] = None) -> str:
        with self.lock:
            self.last_used = time.time()
            self.turns += 1
            return self.agent.ask(text, on_text=on_text)


class SessionManager(object):
    """会话池：最多保留 max_sessions 个，超出时淘汰最久未用的。"""

    def __init__(self, factory: Callable[[], object], max_sessions: int = 16,
                 idle_seconds: int = 3600):
        self.factory = factory
        self.max_sessions = max(1, int(max_sessions))
        self.idle_seconds = max(60, int(idle_seconds))
        self._sessions: Dict[str, Session] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> Session:
        session_id = session_id or "default"
        with self._lock:
            self._evict()
            session = self._sessions.get(session_id)
            if session is None:
                session = Session(session_id, self.factory())
                self._sessions[session_id] = session
                agent_log.log("RUNTIME", "新建会话", session=session_id,
                              total=len(self._sessions))
            return session

    def ask(self, session_id: str, text: str, on_text=None) -> str:
        return self.get(session_id).ask(text, on_text=on_text)

    def drop(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def _evict(self) -> None:
        """回收空闲会话；仍然超出上限时淘汰最久未使用的。"""
        now = time.time()
        for key in [k for k, s in self._sessions.items()
                    if now - s.last_used > self.idle_seconds]:
            self._sessions.pop(key, None)
        while len(self._sessions) >= self.max_sessions:
            oldest = min(self._sessions.items(), key=lambda item: item[1].last_used)
            self._sessions.pop(oldest[0], None)
            agent_log.log("RUNTIME", "淘汰最久未用会话", session=oldest[0])

    def stats(self) -> dict:
        with self._lock:
            return {
                "count": len(self._sessions),
                "max_sessions": self.max_sessions,
                "idle_seconds": self.idle_seconds,
                "sessions": [{"id": s.id, "turns": s.turns,
                              "idle": int(time.time() - s.last_used)} for s in self._sessions.values()],
            }
