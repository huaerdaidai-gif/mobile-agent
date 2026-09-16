# -*- coding: utf-8 -*-
"""HostProfile / ResourceSnapshot / Host 接口。"""

import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class HostProfile:
    """静态描述：这台机器是什么、能干什么。"""

    platform: str = "unknown"          # 例如 "termux" / "linux" / "darwin"
    architecture: str = "unknown"      # 例如 "aarch64" / "arm64"
    cpu: str = "unknown"               # CPU 型号或核心数描述
    ram_bytes: int = 0                 # 物理内存总量
    storage_bytes: int = 0             # 工作目录所在磁盘可用空间
    supported_backends: List[str] = field(default_factory=list)  # 例如 ["cpu", "llama.cpp"]
    hostname: str = ""

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "architecture": self.architecture,
            "cpu": self.cpu,
            "ram_bytes": self.ram_bytes,
            "storage_bytes": self.storage_bytes,
            "supported_backends": list(self.supported_backends),
            "hostname": self.hostname,
        }


@dataclass
class ResourceSnapshot:
    """动态快照：这一刻的资源情况（拿不到的字段是 None）。"""

    ram_available: Optional[int] = None      # 可用内存（字节）
    cpu_usage: Optional[float] = None        # 0.0 ~ 1.0
    temperature: Optional[float] = None      # 摄氏度
    battery: Optional[float] = None          # 0 ~ 100
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "ram_available": self.ram_available,
            "cpu_usage": self.cpu_usage,
            "temperature": self.temperature,
            "battery": self.battery,
            "timestamp": self.timestamp,
        }


class Host(object):
    """Host 接口：只需要提供「静态描述」和「动态快照」。"""

    name = "host"

    def profile(self) -> HostProfile:
        raise NotImplementedError

    def snapshot(self) -> ResourceSnapshot:
        raise NotImplementedError
