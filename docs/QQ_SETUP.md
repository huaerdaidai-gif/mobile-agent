# QQ Bot 接入步骤（OneBot v11）

Mobile Agent 不直接实现 QQ 协议，而是对接 **OneBot v11 HTTP** 实现
（NapCat / Lagrange / LLOneBot 都可以）。链路是：

```
QQ 用户消息
  → OneBot 实现（负责登录 QQ）
  → HTTP POST 事件到 Gateway: /qq/onebot
  → Gateway → Agent Core → Model
  → 回复通过 OneBot HTTP API（send_private_msg / send_group_msg）发回 QQ
```

这样 QQ 只是 Adapter，未来换 Telegram/Discord 只需要再写一个 Adapter。

## 一、Mobile Agent 侧需要什么

OneBot 实现必须满足两点：

1. **HTTP API 可用**：我们能 POST `send_private_msg` / `send_group_msg` / `get_login_info`
   （对应配置项 `qq.api_base`）。
2. **事件推送开启（HTTP POST 模式）**：把消息事件 POST 到
   `http://<gateway.host>:<gateway.port>/qq/onebot`。

如果配置了 `qq.token`，两个方向都要带：
`?access_token=xxx` 或 `Authorization: Bearer xxx`。

## 二、配置（config.yaml）

```yaml
gateway:
  # 如果 OneBot 实现跑在同一台手机上，用 127.0.0.1 即可
  # 如果跑在电脑上，需要手机能被电脑访问 → 改成 0.0.0.0 并配置 token
  host: 0.0.0.0
  port: 8787
  auth: token
  token: ${MOBILE_AGENT_GATEWAY_TOKEN}

qq:
  enabled: true
  adapter: onebot
  api_base: http://127.0.0.1:3000      # OneBot 的 HTTP API 地址
  token: ${MOBILE_AGENT_QQ_TOKEN}      # OneBot 的 access_token（强烈建议设置）
  self_id: ""                          # 机器人 QQ 号，可留空
  require_at: true                     # 群里 @ 才回复
  reply_group: true
  at_sender: false                     # 回复时是否 @ 提问者
  max_chars: 1200                      # 单条消息上限，超出自动分片
  allow_users: ""                      # 白名单 QQ 号，逗号分隔，留空为不限制
```

敏感信息通过环境变量传入（不写进文件）：

```bash
export MOBILE_AGENT_QQ_TOKEN="你的onebot_token"
export MOBILE_AGENT_GATEWAY_TOKEN="随便一个长随机串"
```

> `config.yaml` 里的 `${VAR}` 会在启动时展开；未设置且没有默认值时为空。

## 三、OneBot 实现（以 NapCat 为例）

> 版本/命令以 NapCat 官方文档为准，这里只说明**需要打开的功能**。

1. 在 Termux（或电脑）安装并启动 OneBot 实现，扫码登录你的 QQ 账号。
2. 打开 **HTTP 服务端**（HTTP API），端口例如 `3000`，记下 access_token。
3. 打开 **HTTP 推送/上报（HTTP POST）**，地址填：
   - OneBot 与 Agent 在同一台手机：`http://127.0.0.1:8787/qq/onebot`
   - OneBot 在电脑、Agent 在手机：`http://<手机IP>:8787/qq/onebot`
   并填写同一个 access_token。
4. 手机侧启动 Agent：

```bash
mobile-agent start
mobile-agent status
```

健康检查里的 `qq` 一项会调用 `get_login_info`，能返回机器人 QQ 号就说明通了：

```
  qq         OK   bot_id=123456789
```

## 四、验证

1. 私聊机器人发「你好」→ 应该收到 Agent 回复。
2. 群里 @机器人 发「现在几点？」→ 应该看到工具调用后的回答。
3. 手机侧日志（`runtime/state/gateway.log`）会按组件打标签：

```
... [QQ] 收到消息 session=qq:private:xxx user=xxx chars=2
... [GATEWAY] access "POST /qq/onebot HTTP/1.1" 200 -
```

## 五、常见问题

| 现象 | 排查 |
| --- | --- |
| `/health` 里 `qq` 显示 FAIL | `qq.api_base` 不对、OneBot 没启动、或 token 不一致 |
| 发消息没反应 | OneBot 的 HTTP POST 上报地址没配、或 `require_at: true` 而群里没 @ |
| 回复很慢 | 看 `/health` 的 model 耗时和 `runtime/state/gateway.log`；真机推理本身较慢 |
| 群里所有人都能用 | 用 `qq.allow_users: "12345,67890"` 限制，或群里设 `require_at: true` |
| 不想暴露到局域网 | 让 OneBot 与 Agent 跑在同一台设备，`gateway.host` 保持 `127.0.0.1` |
