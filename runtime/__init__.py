# -*- coding: utf-8 -*-
"""Runtime 层：配置、日志、会话、服务编排、健康检查。

这一层只做「把已有的 Core（agent.py / llm.py / tools）跑起来」需要的胶水，
不包含任何平台判断（Termux/Xiaomi/Android 全部在 adapter 层）。
"""
