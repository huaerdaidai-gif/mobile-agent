# -*- coding: utf-8 -*-
"""Gateway 层：所有外部入口（HTTP / QQ）都先到这里，再由它调用 Agent Core。"""

from gateway.base import ChannelMessage, GatewayAdapter, NoAuth, TokenAuth

__all__ = ["ChannelMessage", "GatewayAdapter", "NoAuth", "TokenAuth"]
