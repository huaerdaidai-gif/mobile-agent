# -*- coding: utf-8 -*-
"""Adapter 层：所有「具体平台 / 具体协议」的实现都放在这里。

Core 只依赖 host/、model/、gateway/、capability/ 里的抽象，
具体实现（Termux、OneBot、OpenAI 兼容 API）在这一层被注入。
"""
