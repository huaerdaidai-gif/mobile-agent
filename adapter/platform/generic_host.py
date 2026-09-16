# -*- coding: utf-8 -*-
"""通用 Host：Linux / macOS 上都能用的基础实现。"""

import os
import shutil
import sys

from host.base import Host, HostProfile, ResourceSnapshot


def _cpu_model() -> str:
    """尽量拿到 CPU 型号，拿不到就退回核心数描述。"""
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.lower().startswith(("model name", "hardware", "processor")) and ":" in line:
                    value = line.split(":", 1)[1].strip()
                    if value and not value.isdigit():
                        return value
    except OSError:
        pass
    return "%d cores" % (os.cpu_count() or 1)


def _ram_total() -> int:
    """物理内存总量（字节）。"""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        pass
    try:  # Android/Linux 兜底
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _ram_available() -> int:
    """可用内存（字节）。"""
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _cpu_usage() -> float:
    """CPU 使用率：优先 /proc/stat 两次采样，拿不到就用 loadavg。"""
    try:
        with open("/proc/stat", "r", encoding="utf-8") as handle:
            first = handle.readline().split()[1:8]
        import time as _time
        _time.sleep(0.1)
        with open("/proc/stat", "r", encoding="utf-8") as handle:
            second = handle.readline().split()[1:8]
        first_values = [int(v) for v in first]
        second_values = [int(v) for v in second]
        idle_delta = second_values[3] - first_values[3]
        total_delta = sum(second_values) - sum(first_values)
        if total_delta > 0:
            return max(0.0, min(1.0, 1.0 - idle_delta / float(total_delta)))
    except (OSError, ValueError, IndexError):
        pass
    try:
        load = os.getloadavg()[0]
        return max(0.0, min(1.0, load / float(os.cpu_count() or 1)))
    except (OSError, AttributeError):
        return None


class GenericHost(Host):
    """通用 Host：不依赖任何平台专用命令。"""

    name = "generic"
    platform_name = ""          # 留空则用 sys.platform（darwin / linux ...）
    backends = ("cpu",)

    def __init__(self, workdir: str = "."):
        self.workdir = workdir

    def profile(self) -> HostProfile:
        try:
            uname = os.uname()
            architecture = uname.machine
            hostname = uname.nodename
        except AttributeError:  # 理论上不会发生（POSIX 都有）
            architecture, hostname = "unknown", ""
        try:
            storage = shutil.disk_usage(self.workdir).free
        except OSError:
            storage = 0
        return HostProfile(
            platform=self.platform_name or sys.platform,
            architecture=architecture,
            cpu=_cpu_model(),
            ram_bytes=_ram_total(),
            storage_bytes=storage,
            supported_backends=list(self.backends),
            hostname=hostname,
        )

    def snapshot(self) -> ResourceSnapshot:
        return ResourceSnapshot(
            ram_available=_ram_available() or None,
            cpu_usage=_cpu_usage(),
            temperature=None,
            battery=None,
        )
