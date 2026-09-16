# -*- coding: utf-8 -*-
"""Stage 3 测试：调度器（串行 / I/O 并行 / 模型并行默认关闭 / 失败隔离）。"""

import time
import unittest

from runtime.scheduler import KIND_IO, KIND_MODEL, Scheduler, Task


def sleep_task(name, seconds, kind=KIND_IO):
    def run():
        time.sleep(seconds)
        return {"ok": True, "name": name}
    return Task(name, run, kind)


class SchedulerTest(unittest.TestCase):

    def test_sequential_runs_in_order(self):
        scheduler = Scheduler()
        order = []
        tasks = [Task("a", lambda: order.append("a")), Task("b", lambda: order.append("b"))]
        result = scheduler.run_sequential(tasks)
        self.assertEqual(order, ["a", "b"])
        self.assertEqual(result.mode, "sequential")
        self.assertEqual(set(result.results), {"a", "b"})

    def test_io_parallel_is_faster_than_sequential(self):
        scheduler = Scheduler(max_parallel_tasks=2)
        tasks = [sleep_task("a", 0.30), sleep_task("b", 0.30)]
        parallel = scheduler.run_io_parallel(tasks)
        self.assertEqual(parallel.mode, "parallel")
        self.assertLess(parallel.total_ms, 550, "两个 0.3s 的 I/O 任务应该并行完成")
        self.assertGreater(parallel.parallel_saved_ms, 100)
        sequential = scheduler.run_sequential([sleep_task("a", 0.30), sleep_task("b", 0.30)])
        self.assertGreater(sequential.total_ms, parallel.total_ms)

    def test_model_tasks_stay_sequential_by_default(self):
        scheduler = Scheduler(parallel_models=False)
        tasks = [sleep_task("m1", 0.20, KIND_MODEL), sleep_task("m2", 0.20, KIND_MODEL)]
        result = scheduler.run(tasks)
        self.assertEqual(result.mode, "sequential")
        self.assertGreaterEqual(result.total_ms, 380, "单 slot 下模型任务不能并行")

    def test_parallel_models_can_be_enabled_for_io_like_model_tasks(self):
        scheduler = Scheduler(parallel_models=True)
        tasks = [sleep_task("m1", 0.25, KIND_MODEL), sleep_task("m2", 0.25, KIND_MODEL)]
        result = scheduler.run(tasks)
        self.assertEqual(result.mode, "parallel")
        self.assertLess(result.total_ms, 480)

    def test_single_task_is_sequential(self):
        scheduler = Scheduler()
        result = scheduler.run([sleep_task("solo", 0.01)])
        self.assertEqual(result.mode, "sequential")

    def test_mixed_keeps_model_sequential(self):
        scheduler = Scheduler()
        tasks = [sleep_task("io1", 0.20), sleep_task("io2", 0.20), sleep_task("m", 0.20, KIND_MODEL)]
        result = scheduler.run(tasks)
        self.assertEqual(result.mode, "mixed")
        self.assertEqual(set(result.results), {"io1", "io2", "m"})

    def test_failure_is_isolated(self):
        scheduler = Scheduler()

        def boom():
            raise RuntimeError("炸了")

        result = scheduler.run_io_parallel([Task("bad", boom), sleep_task("good", 0.05)])
        self.assertFalse(result.results["bad"]["ok"])
        self.assertIn("炸了", result.results["bad"]["error"])
        self.assertTrue(result.results["good"]["ok"])

    def test_parallel_map_helper(self):
        scheduler = Scheduler()
        result = scheduler.parallel_map({"a": lambda: 1, "b": lambda: 2})
        self.assertEqual(result.results, {"a": 1, "b": 2})

    def test_stats_and_info(self):
        scheduler = Scheduler(max_parallel_tasks=3, max_active_models=1)
        scheduler.run_sequential([sleep_task("a", 0.01)])
        scheduler.run_io_parallel([sleep_task("a", 0.05), sleep_task("b", 0.05)])
        info = scheduler.info()
        self.assertEqual(info["max_active_models"], 1)
        self.assertFalse(info["parallel_models"])
        self.assertEqual(info["stats"]["sequential_runs"], 1)
        self.assertEqual(info["stats"]["parallel_runs"], 1)

    def test_build_scheduler_from_config(self):
        from runtime.scheduler import build_scheduler
        scheduler = build_scheduler({"scheduler": {"max_parallel_tasks": 3,
                                                  "max_active_models": 2,
                                                  "parallel_models": True}})
        self.assertEqual(scheduler.max_parallel_tasks, 3)
        self.assertEqual(scheduler.max_active_models, 2)
        self.assertTrue(scheduler.parallel_models)
        default = build_scheduler({})
        self.assertFalse(default.parallel_models)
        self.assertEqual(default.max_active_models, 1)


if __name__ == "__main__":
    unittest.main()
