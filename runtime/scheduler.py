# -*- coding: utf-8 -*-
"""轻量任务调度（Stage 3）。

规则（严格按真机约束）：
  - 当前 llama.cpp total_slots = 1 → **模型推理默认不并行**（并行只会排队，不会更快）；
  - I/O 任务（HTTP 探测、网络查询）可以并行，能真正省时间；
  - 有依赖的任务（Vision → Primary）必须串行，由 steps 顺序保证；
  - 不创建常驻线程：批量用 ThreadPoolExecutor，用完即关。
"""

import concurrent.futures
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List

from runtime import logging as agent_log

KIND_IO = "io"
KIND_MODEL = "model"


@dataclass
class Task:
    """一个可执行任务。"""

    name: str
    run: Callable[[], object]
    kind: str = KIND_IO


@dataclass
class ScheduleResult:
    """调度结果：每个任务的结果与耗时，以及整体统计。"""

    results: Dict[str, object] = field(default_factory=dict)
    timings: Dict[str, int] = field(default_factory=dict)
    mode: str = "sequential"
    total_ms: int = 0
    parallel_saved_ms: int = 0


class Scheduler(object):
    """串行 / I/O 并行调度器。"""

    def __init__(self, max_parallel_tasks: int = 2, max_active_models: int = 1,
                 parallel_models: bool = False, model_timeout: int = 300,
                 tool_timeout: int = 15):
        self.max_parallel_tasks = max(1, int(max_parallel_tasks))
        self.max_active_models = max(1, int(max_active_models))
        self.parallel_models = bool(parallel_models)
        self.model_timeout = int(model_timeout)
        self.tool_timeout = int(tool_timeout)
        self.stats = {"runs": 0, "parallel_runs": 0, "sequential_runs": 0,
                      "tasks": 0, "parallel_saved_ms": 0}

    # ---- 对外 ----

    def run_sequential(self, tasks: List[Task]) -> ScheduleResult:
        """顺序执行（默认路径，模型调用必须走这里）。"""
        result = ScheduleResult(mode="sequential")
        started = time.time()
        for task in tasks:
            task_started = time.time()
            result.results[task.name] = self._safe_run(task)
            result.timings[task.name] = int((time.time() - task_started) * 1000)
        result.total_ms = int((time.time() - started) * 1000)
        self.stats["sequential_runs"] += 1
        self._record(result)
        return result

    def run_io_parallel(self, tasks: List[Task], max_workers: int = None) -> ScheduleResult:
        """并行执行纯 I/O 任务（不含模型推理）。"""
        result = ScheduleResult(mode="parallel")
        started = time.time()
        workers = max(1, min(max_workers or self.max_parallel_tasks, len(tasks)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers,
                                                   thread_name_prefix="sched") as pool:
            futures = {}
            for task in tasks:
                task_started = time.time()
                futures[pool.submit(self._safe_run, task)] = (task, task_started)
            for future in concurrent.futures.as_completed(futures):
                task, task_started = futures[future]
                result.timings[task.name] = int((time.time() - task_started) * 1000)
                try:
                    result.results[task.name] = future.result()
                except Exception as exc:  # _safe_run 已兜底，这里仅防御
                    result.results[task.name] = {"ok": False, "error": str(exc)[:160]}
        result.total_ms = int((time.time() - started) * 1000)
        sequential_estimate = sum(result.timings.values())
        result.parallel_saved_ms = max(0, sequential_estimate - result.total_ms)
        self.stats["parallel_runs"] += 1
        self.stats["parallel_saved_ms"] += result.parallel_saved_ms
        self._record(result)
        return result

    def run(self, tasks: List[Task]) -> ScheduleResult:
        """自动选择：单个任务直接串行；模型任务在单 slot 下必须串行。"""
        tasks = list(tasks)
        if len(tasks) <= 1:
            return self.run_sequential(tasks)
        io_tasks = [task for task in tasks if task.kind == KIND_IO]
        model_tasks = [task for task in tasks if task.kind == KIND_MODEL]
        if not model_tasks:
            return self.run_io_parallel(io_tasks)
        if self.parallel_models:
            # 只有显式打开（多模型 / 多 slot）才允许模型任务并行
            return self.run_io_parallel(tasks)
        if not io_tasks:
            agent_log.log("RUNTIME", "检测到模型任务且 parallel_models=false，按串行执行",
                          level="DEBUG")
            return self.run_sequential(tasks)
        return self._run_mixed(io_tasks, model_tasks)

    def _run_mixed(self, io_tasks: List[Task], model_tasks: List[Task]) -> ScheduleResult:
        """混合任务：I/O 部分并行，模型部分严格串行（保持依赖顺序）。"""
        merged = ScheduleResult(mode="mixed")
        started = time.time()
        io_result = self.run_io_parallel(io_tasks)
        model_result = self.run_sequential(model_tasks)
        merged.results.update(io_result.results)
        merged.results.update(model_result.results)
        merged.timings.update(io_result.timings)
        merged.timings.update(model_result.timings)
        merged.total_ms = int((time.time() - started) * 1000)
        merged.parallel_saved_ms = io_result.parallel_saved_ms
        return merged

    def parallel_map(self, named_functions: Dict[str, Callable[[], object]]) -> ScheduleResult:
        """便捷入口：{名称: 函数} → 并行执行（全部当 I/O 任务）。"""
        return self.run_io_parallel([Task(name, func, KIND_IO)
                                     for name, func in named_functions.items()])

    # ---- 内部 ----

    @staticmethod
    def _safe_run(task: Task):
        try:
            return task.run()
        except Exception as exc:  # 任何任务失败都不影响其他任务
            return {"ok": False, "error": str(exc)[:200]}

    def _record(self, result: ScheduleResult) -> None:
        self.stats["runs"] += 1
        self.stats["tasks"] += len(result.results)

    def info(self) -> dict:
        return {"max_parallel_tasks": self.max_parallel_tasks,
                "max_active_models": self.max_active_models,
                "parallel_models": self.parallel_models,
                "model_timeout": self.model_timeout,
                "tool_timeout": self.tool_timeout,
                "stats": dict(self.stats)}


def build_scheduler(config: dict = None) -> Scheduler:
    """从配置构造调度器（缺省保守）。"""
    section = (config or {}).get("scheduler") or {}
    return Scheduler(max_parallel_tasks=int(section.get("max_parallel_tasks", 2) or 2),
                     max_active_models=int(section.get("max_active_models", 1) or 1),
                     parallel_models=bool(section.get("parallel_models", False)),
                     model_timeout=int(section.get("model_timeout", 300) or 300),
                     tool_timeout=int(section.get("tool_timeout", 15) or 15))
