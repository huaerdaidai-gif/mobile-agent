# Mobile Agent —— Portable Agent MVP

核心仍是 v0.25.1（行为不变），外面加了一层「可安装、可启动、可接 QQ」的 MVP 骨架。

## Portable Agent MVP（本轮新增）

```
QQ / HTTP / CLI
      ↓
  gateway/           统一入口：request / response / session / streaming / authentication
      ↓
  runtime/service    组装 + 会话管理（AgentService / SessionManager）
      ↓
  agent.py (Core)    工具循环、记忆、上下文预算、流式闸门
      ↓
  model/ + adapter/model/openai_compatible     OpenAI 兼容 Provider（llama.cpp 等）

  旁路：
  host/     + adapter/platform/termux_host     Host 抽象与 Termux 适配
  capability/ + adapter/capability/tool_*      工具 → Capability（只包装，不改执行）
  extension/                                   学习/设备/调参/插件等未来接口（默认关闭）
  adapter/qq/onebot                             QQ Bot 适配器（OneBot v11 HTTP）
```

分层原则（Core 依赖倒置）：

| Core 不依赖 | 由谁负责 |
| --- | --- |
| QQ | `adapter/qq/` 适配器 |
| Termux / Android / 机型 | `adapter/platform/` 的 Host 实现 |
| Qwen / llama.cpp | `model/` 抽象 + `adapter/model/openai_compatible.py` |
| HTTP 框架 | `gateway/`（纯标准库 http.server） |

### 目录速览

```
runtime/     config.py（配置+环境变量） logging.py（组件日志+打码） service.py session.py state/
host/        base.py（HostProfile / ResourceSnapshot / Host 接口）
model/       base.py（ModelProfile / ModelProvider 接口）
capability/  base.py（Capability / CapabilityRegistry）
gateway/     base.py（GatewayAdapter / Auth） server.py（HTTP） health.py
adapter/     platform/（GenericHost、TermuxHost） model/（OpenAICompatible） qq/（OneBot） capability/
extension/   interfaces.py（LearningEngine / DeviceRegistry / DeviceAuthorization / ParameterTuner / Plugin / PolicyGate）
cli.py       mobile-agent start|stop|status|test|health|chat|config
scripts/     install-termux.sh / start-termux.sh / stop-termux.sh / status-termux.sh
tests/       49 个离线测试（含假模型、假 OneBot）
docs/        QQ_SETUP.md
```

### 安装（Termux）

```bash
# 1) 把项目目录拷进手机（git clone / adb push / Termux 里直接编辑都行）
cd mobile-agent

# 2) 一键安装（必要时自动装 python）
bash scripts/install-termux.sh --auto-deps
# 可选：需要唤醒锁/通知/电池温度时再加 --with-termux-api

# 3) 改配置（模型地址、QQ 配置）
nano config.yaml

# 4) 启动并查看状态
mobile-agent start
mobile-agent status      # 期望：Host OK / Model OK / Gateway OK
mobile-agent test        # 离线测试 + 模型连通性检查
```

安装脚本会：检查 python → 可选装 termux-api → 建 `memory/`、`runtime/state/` →
没有 config.yaml 时从 `config.example.yaml` 生成 → 把 `cli.py` 链接成 `$PREFIX/bin/mobile-agent` →
跑一遍离线自检。

### 命令行

| 命令 | 作用 |
| --- | --- |
| `mobile-agent start` | 后台启动 Gateway（写 `runtime/state/gateway.pid`，日志 `runtime/state/gateway.log`） |
| `mobile-agent stop` | 停止 Gateway |
| `mobile-agent status` | 进程 + 健康检查（Agent/Model/Gateway/QQ/Host/Capability） |
| `mobile-agent test [--offline]` | 跑测试套件（默认再测一次模型连通性） |
| `mobile-agent health` | 打印 `/health` 的 JSON |
| `mobile-agent chat` | 不进 Gateway，直接终端聊天 |
| `mobile-agent config` | 打印生效配置（敏感值已打码） |

### HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查（`?fast=1` 跳过模型探测） |
| POST | `/api/chat` | `{"text": "...", "session_id": "..."}` → `{"reply": "..."}` |
| POST | `/api/chat/stream` | 同样入参，返回流式纯文本（读到 EOF 结束） |
| POST | `/qq/onebot` | OneBot v11 事件推送入口 |
| POST | `/admin/<action>` | 管理接口占位：默认 403，由 PolicyGate 拦截 |

认证：`gateway.auth: none`（仅监听 127.0.0.1 时用）或 `token`（`Authorization: Bearer ...`）。

### 日志

按组件打标签，敏感值自动打码，输出到 stdout 和 `runtime/state/gateway.log`：

```
2026-09-16 13:39:47 INFO  [MODEL] 上下文探测完成 context=2048 source=GET /v1/models
2026-09-16 13:39:48 INFO  [GATEWAY] 启动监听 http://127.0.0.1:8787（认证=none）
2026-09-16 13:39:48 INFO  [QQ] 收到消息 session=qq:private:20002 user=20002 chars=2
```

组件：`CORE / MODEL / TOOL / GATEWAY / QQ / HOST / RUNTIME`。

### 安全与扩展

- 高风险接口（`/admin/*`）默认关闭：`extension.admin_enabled: false`，开启也要过 token 认证；
- 未来所有远程控制按 `Authentication → Authorization → Policy → Executor` 链路：
  `gateway.auth` → identity → `PolicyGate` → 执行器；
- 未来能力的接口已经留好（`extension/interfaces.py`），本轮全部不实现：
  `LearningEngine`、`DeviceRegistry`、`DeviceAuthorization`、`ParameterTuner`、`Plugin`。

### 本轮验收结果

| 项 | 结果 |
| --- | --- |
| macOS：`mobile-agent start/stop/status` | OK（后台进程 + pid 文件 + 健康检查） |
| 普通聊天 / 连续聊天（同一会话） | OK（真实 Qwen2.5-3B：`你好`、`1+1等于2`） |
| Memory | OK（`记住…` 直接写 memory.json，0 次模型调用） |
| Tool | OK（`现在几点？` → 调用 time 工具 → 「现在的时间是2026年9月16日，星期三的13点40分26秒」） |
| Streaming | OK（`/api/chat/stream` 流式返回） |
| QQ 链路 | OK（OneBot 事件 → Gateway → Agent → 回复发回 OneBot HTTP API） |
| 离线测试 | 49/49 通过（含假模型、假 OneBot，不依赖真机） |

> 说明：本轮 QQ 链路是用「假 OneBot 实现」验证的（我没有你的 QQ 账号）；
> 换成真实 NapCat/Lagrange 只需按 `docs/QQ_SETUP.md` 填 `qq.api_base` 与 token。

一个跑在手机 Termux 上、配合 llama.cpp 使用的**轻量级 Agent Harness**。
零框架、零数据库、零 Web 服务、无线程，纯 Python。

## 版本变化

| 版本 | 能力 |
| --- | --- |
| v0.1 | 工具调用循环、JSON 工具协议、上下文裁剪、记忆写入、Shell 白名单、文件路径限制、max_steps 防死循环 |
| v0.2 | 记忆回灌、流式输出（SSE）、简洁工具状态行、会话历史裁剪、非法 JSON 兜底、Termux 检测、`/status` |
| v0.25 | 统一工具协议 `{"type": "tool_call", "name": ..., "arguments": {...}}`、工具注册表、严格解析器、参数校验、统一的 `{ok, result}` 返回 |
| v0.25.1 | 上下文自动探测（min(配置, 服务器)）、prompt 精简与预算控制、记住意图直写 memory、聊天工具门控、JSON 约束重试、全角标点归一化、工具结果截断、性能/精度基准脚本 |

## 设计原则

| 原则 | v0.2 的做法 |
| --- | --- |
| 极低资源占用 | 标准库优先，无后台线程、无数据库、无缓存框架；输出与记忆都有长度上限 |
| 支持 1B~4B 小模型 | 工具协议是单个 JSON 对象；非法 JSON 只提醒一次；关闭工具时提示词里干脆不提工具 |
| 不依赖大型 Agent 框架 | 无 LangChain / LlamaIndex / AutoGen，核心逻辑全部可读可改 |
| Python 实现 | Python 3.8+，无编译步骤，Termux 上 `pkg install python` 即可 |
| 可迁移到 Android Termux | 未使用平台相关 API；`requests` 缺失时自动回退标准库 `urllib`（流式同样可用） |
| OpenAI Compatible API | 只调用 `POST {base_url}/chat/completions`，支持 `stream=true` |

## 目录结构

```
mobile-agent/
├── main.py              # CLI 入口：加载配置、流式显示、/status 等命令
├── agent.py             # Agent Core：工具循环、记忆回灌、历史裁剪、流式闸门
├── llm.py               # LLM 接口层：chat() 与 chat_stream()（SSE 解析）
├── tool_parser.py       # 严格工具解析器：只认统一协议，验证 type/name/arguments
├── config.yaml          # 配置：接口、模型、流式、记忆、工具
├── requirements.txt     # 可选依赖（不装也能跑）
├── README.md
├── platform/            # Termux 兼容层
│   ├── __init__.py
│   └── termux.py        # is_termux / has_termux_api / wake_lock / notification / battery_status
├── tools/
│   ├── __init__.py
│   ├── registry.py      # 工具注册表：名字 / 用途 / 参数，提示词与 /tools 都从它生成
│   ├── shell.py         # run_command(command)：白名单安全 shell
│   ├── file.py          # read_file / write_file：限项目目录内
│   └── time.py          # get_current_time()
├── work/
│   ├── benchmark_v0251.py        # 性能基准（耗时 / 首 token / prompt / completion tokens）
│   └── test_precision_v0251.py   # 30 条精度测试（聊天 / 工具 / 记忆 / 异常）
└── memory/
    └── memory.json      # 持久化记忆（每轮问答追加一条）
```

> 说明：`platform/` 与标准库的 `platform` 模块同名，会遮蔽标准库版本。
> 该包的 `__init__.py` 会把标准库 `platform` 的公开属性桥接进来，
> 因此 `platform.system()` 这类调用（包括第三方库内部的调用）仍然正常。

## 快速开始

### 1. 启动 llama.cpp server（OpenAI 兼容接口）

```bash
llama-server -m qwen2.5-3b-instruct-q4_k_m.gguf \
  --host 127.0.0.1 --port 8080 --ctx-size 4096
```

### 2. 安装依赖（可选）

```bash
pip install requests pyyaml
```

不安装也完全可以运行：HTTP 走标准库 `urllib`，配置走内置的极简 YAML 解析。

### 3. 运行

```bash
cd mobile-agent
python main.py
```

```
Mobile Agent v0.25.1
------------------------------------
模型接口: http://127.0.0.1:8080/v1
模型名称: qwen2.5-3b-instruct-q4_k_m.gguf
HTTP 客户端: urllib（标准库回退）（流式输出: 开）
输入 exit 退出，/status 查看状态，/clear 清空上下文，/tools 查看工具
上下文预算: 2048（服务器限制）
system prompt: 约 150 tokens

你> 现在几点？
[调用工具: time]
Agent> 现在的时间是 2026-09-16 09:05:12（星期三，时区 CST）。
```

`/status` 输出：

```
Mobile Agent v0.25.1
模型：
qwen2.5-3b-instruct-q4_k_m.gguf
服务器：
127.0.0.1:8080
上下文：
配置 4096
服务器 2048
实际 2048（服务器限制）
Stream：on
Memory：on（5条）
Tools：on
Tool result limit：4000 chars
输出上限：512 tokens
system prompt：约 150 tokens（上限 300）
Termux：no（termux-api 不可用）
HTTP 客户端：urllib（标准库回退）
```

### 4. 命令行命令

| 命令 | 作用 |
| --- | --- |
| `exit` / `quit` / `退出` | 结束程序 |
| `/status` | 显示版本、LLM 接口、Stream、Memory、Termux、Tools 状态（不显示任何密钥） |
| `/clear` | 清空本次会话上下文（不动 memory.json） |
| `/tools` | 查看工具清单（内容来自 `tools/registry.py`） |

`/tools` 输出示例：

```
当前工具：
time
  获取当前时间
shell
  执行允许的shell命令
file
  文件读取写入
```

## 记忆机制

v0.25.1 起，「记住」类请求**不再经过模型**：

```
你> 记住我是用小米12运行本地AI节点
[记忆] 已保存到 memory.json（未调用模型）
Agent> 已记住。
```

命中 `记住 / 记下来 / 记一下 / 记录一下 / 请记住 / 帮我记住 / 帮我记 / 以后记得` 开头的输入，
Agent 直接写 `memory.json` 并回复「已记住。」——既避免模型误用 `file` 写出
`小米12.txt` 这类无关文件，也省掉一次推理。

另外，**执行过工具的轮次不再写入记忆**：时间、目录、文件内容都是易变信息，
写进记忆会污染后续会话（真机实测出现过模型复述记忆里的过期时间）。

记忆分两层，互不干扰：

1. **存储层**：每轮问答结束后追加一条记录到 `memory/memory.json`，
   条数上限由 `agent.max_memory_entries` 控制（默认 50），写入用「临时文件 + 替换」的原子写法。
2. **回灌层**：启动时读取最近 `memory.max_items` 条（默认 10），以结构化文本块塞进 system prompt：

```
【历史记忆】（最近 3 条，仅供参考；若与当前对话冲突，以当前对话为准）
- 用户：你好 ／ 你：我是一个运行在手机 Termux 上的轻量级 Agent。
- 用户：现在几点？ ／ 你：现在是 2026-09-16 06:55:39（星期三）。
```

配额与健壮性：

- 记忆块最多占 `max_context` 的 **1/4**，超出时从最旧的记录开始丢，**最新记录一定保留**；
- 单条记录也会截断（用户 ≤300 字、回答 ≤600 字，回灌时再压到 60/120 字），不会把整个 `memory.json` 塞进上下文；
- `memory.json` 不存在 → 自动创建；
- `memory.json` 损坏 → 忽略损坏内容继续运行（`/status` 会标注「记忆文件损坏，已忽略」），下一次成功回答时会重写成合法文件；
- 记录结构不完整（缺 user、不是字典等）→ 跳过该条。

把 `memory.enabled` 设为 `false` 可以整体关闭记忆：既不回灌，也不写盘。

## 流式输出

## 上下文与性能（v0.25.1 实测）

### 上下文自动探测

启动时会 `GET /v1/models`，拿不到再试 `GET /props`，读服务器真实上下文长度：

```
Mobile Agent v0.25.1
模型接口: http://127.0.0.1:8080/v1
上下文预算: 2048（服务器限制）
system prompt: 约 150 tokens
```

规则是 **实际预算 = min(config.agent.max_context, 服务器上下文)**，`config.yaml` 里保留
`max_context: 4096` 也不会再出现 `request 3002 tokens exceeds available context size 2048`。
探测失败（服务未启动、不支持这两个端点）时沿用配置值，并打印提示。
刻意不使用 `n_ctx_train`——那是训练上下文，不代表服务器开出来的长度。

### Prompt 预算

| 项目 | 预算 | 说明 |
| --- | --- | --- |
| system prompt | ≤300 tokens（实测约 150） | 协议格式只写一次，工具列表每个工具一行 |
| 记忆块 | ≤ 实际上下文 × 10% | 放不下就不回灌，不挤占对话 |
| prompt 总量 | ≤ 实际上下文 × 60% | 其余留给模型输出（`max_output_tokens` 默认 512） |
| 单条工具结果 | ≤ `tool_result_max_chars`（默认 4000 字符）且 ≤ prompt 预算 45% | 超出加 `[工具结果过长，已截断]` |

### 真机实测（小米12 + Qwen2.5-3B-Q4_K_M，2048 上下文）

| 场景 | prompt tokens | 首 token | 总耗时 |
| --- | --- | --- | --- |
| 闲聊「你好」（热缓存） | 136（新增 1） | 0.2–1s | 1.7–3.8s |
| 「现在几点？」（2 次模型调用 + 工具） | 138 + 205 | 0.7s / 4.9s | 10–20s |
| 「读取 README.md」（工具结果被截断后仍约 400 tokens） | 139 + 790 | 1.1s / 90s | 120–140s |

关键结论（都影响调优方向）：

1. **prompt processing 是真机瓶颈**：这台手机约 10–12 token/s，且只对新 token 计费；
   工具结果每多 100 tokens ≈ 多 10 秒。热降频后掉到 2–3 token/s，同样一次请求要几分钟。
2. **prompt cache 很关键**：system prompt 不变时，第二轮开始只处理新增 token（实测新增
   1 token → 首字 0.23s）。因此 v0.25.1 刻意做了两件事：记忆块只在启动时算一次（不在会话中重建）、
   `记住` 写入记忆后不刷新记忆块——之前每写一条记忆都会让整个 prompt 前缀失效，下一轮白等 30 秒。
3. **想更快就调小 `tool_result_max_chars`**：在 2048 上下文手机上建议 800–1200；
   README/config.yaml 这种长文件读取是当前最慢的场景。
4. 手机连续跑 30 分钟后会明显热降频，长任务建议配合 `termux-wake-lock` 并留散热空间。

- 普通回答**边生成边显示**：模型产出多少就显示多少，不再等整段生成完；
- 工具调用**不刷 JSON**：流式过程中一旦出现 `{` 或 `` ` `` 就先暂存，
  等这一轮生成结束再判断——是普通回答就补显，是工具调用就丢弃，只留一行 `[调用工具: shell]`；
- 关闭流式（`stream: false`）时行为一致，只是回答一次性出现；
- 两条 HTTP 路径都实现了 SSE 解析：

| 场景 | 实现 | 是否支持流式 |
| --- | --- | --- |
| 已安装 `requests` | `stream=True` + `iter_lines()` | 支持 |
| 未安装 `requests` | `urllib.request.urlopen()` 逐行读取 | 支持（无需降级） |

流式期间如果连接中断：已经生成的内容会保留并提示「流式响应中断」；
如果中断时是半截工具调用 JSON，则丢弃该内容并给出「模型输出不完整，请重试」。

## 工具调用协议（v0.25 统一协议）

需要工具时，模型只输出一个 JSON 对象，格式固定为三段：

```json
{"type": "tool_call", "name": "time", "arguments": {}}
{"type": "tool_call", "name": "shell", "arguments": {"command": "ls"}}
{"type": "tool_call", "name": "file", "arguments": {"action": "read", "path": "README.md"}}
{"type": "tool_call", "name": "file", "arguments": {"action": "write", "path": "note.txt", "content": "hello"}}
```

解析由 `tool_parser.py` 完成，接受四种输入形态（纯 JSON、```json 代码块、
JSON 前后带少量解释文字、以及非法 JSON → 直接判为普通回答），并严格校验：

| 校验项 | 不满足时 |
| --- | --- |
| `type` == `"tool_call"` | 返回 None，按普通回答处理 |
| `name` 是非空字符串 | 返回 None |
| `arguments` 是 JSON 对象 | 返回 None |

**解析器不猜字段**：`name` 大小写不匹配、`arguments` 写成字符串、少了 `arguments`，
一律不算工具调用，避免小模型输出半成品时被误解成一次真实调用。

v0.25.1 在「严格解析」之外补了三层容错，全部可在 config.yaml 关掉：

| 层 | 触发条件 | 做什么 | 开关 |
| --- | --- | --- | --- |
| 全角标点归一化 | JSON 里出现 `：＂，｛｝` 等全角标点 | 只把标点换成半角再解析 | `tools.normalize_punctuation` |
| 扁平参数兼容 | 参数写在顶层：`{"type":"tool_call","name":"file","action":"read","path":"."}` | 等价视为 `arguments={"action":"read","path":"."}`，参数仍要过注册表校验 | `tools.flat_arguments` |
| JSON 约束重试 | 第一次输出像工具调用但解析失败 | 提醒一次，并要求服务端用 `response_format={"type":"json_object"}` 约束解码后重发 | 无（跟随 `tools.enabled`） |

另外新增 **聊天工具门控**（`performance.chat_tool_gate`）：用户提问里没有
时间/目录/文件等关键词时，模型冒出来的工具调用不执行，改为去掉工具说明重新回答一次。
真机实测把「闲聊误触发工具」从 4/10 降到 **0/10**。

执行结果统一以 `工具结果：{"ok": true, "result": "..."}`（失败时是
`工具结果：{"ok": false, "error": "..."}`）的形式回灌给模型，模型据此决定下一步。
单次提问最多 `agent.max_steps` 轮工具调用，防止小模型死循环。

旧协议（v0.2 的 `{"tool": "shell", "command": "ls"}`）**默认已废弃**：
默认配置下它不会被当作工具调用。需要过渡时可以打开 `tools.legacy_protocol: true`，
由 `tool_parser.parse_legacy_tool_call()` 自动转换成新协议。

## 内置工具与安全边界

| 工具 | 函数 | 返回 | 安全限制 |
| --- | --- | --- | --- |
| `shell` | `run_command(command)` | `{ok, stdout, stderr, returncode}` | 命令白名单；拒绝 `| ; & > < \` $ ( )` 等字符；不使用 `shell=True`；15 秒超时；输出截断到 4000 字符 |
| `file` | `read_file(path)` | `{ok, path, content, truncated}` | 路径必须落在项目目录内 |
| `file` | `write_file(path, content)` | `{ok, path, bytes}` | 同上，只允许写项目目录内 |
| `time` | `get_current_time()` | `{ok, datetime, weekday, timezone, timestamp}` | 无 |

想放行更多命令，编辑 `tools/shell.py` 顶部的 `ALLOWED_COMMANDS`；
把 `tools.enabled` 设为 `false` 则完全禁用工具。

注意这里有**两层返回**：

- 上表是 `tools/*.py` 里原始工具函数的返回（保持 v0.1/v0.2 原样，未改动）；
- Agent 对外统一成 `{"ok": true, "result": "..."}` / `{"ok": false, "error": "..."}`，
  由 `agent.py` 的 `_format_result()` 负责整理，`result` 是给 1B~4B 小模型看的紧凑文本，
  比原始 JSON 更省 token。

工具定义集中在 `tools/registry.py`（名字 / 用途 / 参数 / 示例）。
system prompt 里的工具说明、`/tools` 的输出、参数校验三处共用这一份定义，
新增工具只需要改注册表和 `agent.py` 的 `_execute()` 两处。

## Termux 兼容层

`platform/termux.py` 只做**检测**和**调用 termux-api 命令**，不引入任何 Python 依赖：

| 能力 | 说明 | Termux API 缺失时 |
| --- | --- | --- |
| `is_termux()` | 依据 `TERMUX_VERSION`、`PREFIX`、`/data/data/com.termux/...` 判断 | 返回 `False`，程序照常运行 |
| `is_android()` | 依据内核版本与 Termux 目录判断 | 返回 `False` |
| `has_termux_api()` | 检查 `termux-notification` / `termux-wake-lock` 命令是否存在 | 返回 `False` |
| `wake_lock()` | 申请唤醒锁，避免长任务被系统挂起 | `{"ok": False, "error": "Termux API 不可用（缺少 termux-wake-lock 命令）"}` |
| `notification(title, content)` | 发一条 Termux 通知 | `{"ok": False, "error": "Termux API 不可用（...）"}` |
| `battery_status()` | 读取电池状态 | 返回 `None` |

所有外部命令都有 10 秒超时，失败一律返回结构化结果，不抛异常。
Termux API 是纯可选能力，不装、不配都不影响 Agent 主体功能。

## 错误处理

普通用户不会看到 Python traceback，所有异常都会被转成一句人话：

| 场景 | 行为 |
| --- | --- |
| llama-server 未启动 | `[错误] 无法连接模型服务（...）+ 启动提示`，本轮上下文回滚 |
| server 返回 HTTP 错误 | `[错误] 模型服务返回 HTTP 500：...` |
| 流式中途断开 | 保留已生成内容并提示；半截工具调用则丢弃 |
| 模型返回空内容 | `（模型没有返回任何内容，请重试）` |
| 模型返回非法 JSON / 字段不合规 | 提醒一次让它重发（`[模型输出不是合法的工具调用，已提醒重试一次]`），仍失败就按普通回答处理 |
| 模型调用了不存在的工具 | `[工具失败: abc] 没有名为 abc 的工具。可用工具：time、shell、file`，错误回灌给模型 |
| 模型给的参数不对 | `[工具失败: shell] 缺少必填参数：command（...）`，错误回灌给模型 |
| memory.json 损坏 | 忽略损坏内容继续运行，`/status` 标注，下次写入时重写 |
| Termux API 不存在 | 返回不可用状态，Agent 正常运行 |
| 工具执行失败 | `[工具失败: shell] 原因`，失败信息回灌给模型，让它换做法或如实回答 |
| 其他未预期异常 | `[错误] 运行时异常：...`；需要排查时设 `MOBILE_AGENT_DEBUG=1` 才会抛出 traceback |

## 配置项（config.yaml）

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `stream` | `true` | 是否流式输出（也兼容写在 `llm.stream`） |
| `llm.base_url` | `http://127.0.0.1:8080/v1` | llama.cpp server 地址 |
| `llm.model` | `qwen2.5-3b-instruct-q4_k_m.gguf` | 模型名（llama.cpp 一般会忽略） |
| `llm.temperature` | `0.2` | 采样温度，工具调用建议低一些 |
| `llm.timeout` | `300` | 请求超时（秒）；真机上工具轮次可能超过 120s，故默认放宽 |
| `agent.max_context` | `4096` | 上下文预算（近似 token），需与 `--ctx-size` 一致 |
| `agent.max_steps` | `4` | 单次提问最多工具调用轮数 |
| `agent.max_memory_entries` | `50` | memory.json 保存条数上限 |
| `memory.enabled` | `true` | 是否启用记忆（回灌 + 写入） |
| `memory.max_items` | `10` | 启动时回灌最近多少条记忆 |
| `tools.enabled` | `true` | 是否允许调用工具 |
| `tools.legacy_protocol` | `false` | 是否额外兼容 v0.2 旧协议（过渡用，建议保持 false） |
| `tools.normalize_punctuation` | `true` | 全角标点自动归一化成半角（只改标点，不猜字段） |
| `tools.flat_arguments` | `true` | 接受「参数写在顶层」的等价写法 |
| `performance.auto_detect_context` | `true` | 启动时探测服务器上下文，实际预算取较小值 |
| `performance.tool_result_max_chars` | `4000` | 单条工具结果字符上限（真机建议 800–1200） |
| `performance.memory_max_items` | `5` | 回灌记忆条数上限（优先于 `memory.max_items`） |
| `performance.memory_max_ratio` | `0.10` | 记忆块占实际上下文的比例上限 |
| `performance.system_prompt_max_tokens` | `300` | system prompt 目标上限（超出会告警） |
| `performance.max_output_tokens` | `512` | 模型单次最多生成多少 token |
| `performance.chat_tool_gate` | `true` | 闲聊误触发工具时不执行，改用无工具提示词重答 |
| `model.provider` | `openai-compatible` | 模型提供方（目前只实现这一个） |
| `model.base_url` / `model.name` | 空 | 留空则沿用 `llm.*`；填了就覆盖 |
| `gateway.host` / `gateway.port` | `127.0.0.1` / `8787` | Gateway 监听地址；要接局域网 QQ 实现时改 `0.0.0.0` |
| `gateway.auth` / `gateway.token` | `none` / 空 | 认证方式；token 用 `${MOBILE_AGENT_GATEWAY_TOKEN}` 注入 |
| `gateway.max_sessions` / `idle_seconds` | `16` / `3600` | 会话上限与空闲回收 |
| `qq.enabled` | `false` | 是否启用 QQ 适配器 |
| `qq.api_base` | `http://127.0.0.1:3000` | OneBot v11 HTTP API 地址 |
| `qq.token` | 空 | OneBot access_token，建议用 `${MOBILE_AGENT_QQ_TOKEN}` |
| `qq.require_at` / `reply_group` / `at_sender` | `true` / `true` / `false` | 群聊行为 |
| `qq.max_chars` / `allow_users` | `1200` / 空 | 分片长度与用户白名单 |
| `host.profile` | `auto` | `auto` / `termux` / `generic` |
| `extension.admin_enabled` | `false` | 管理接口开关（高风险，默认关闭） |
| `logging.level` / `logging.file` | `INFO` / 空 | 日志级别与文件（留空只输出 stdout） |

## 测试用例

### 性能基准

```bash
python3 work/benchmark_v0251.py                    # 用 config.yaml 的 base_url
python3 work/benchmark_v0251.py --url http://127.0.0.1:8080/v1 --repeat 2
```

输出每个场景的：请求总耗时、首 token 时间、prompt tokens、completion tokens、
模型调用次数、工具调用次数、是否成功，并给出普通聊天 / 工具调用两类的平均值。

### 30 条精度测试

```bash
python3 work/test_precision_v0251.py               # 聊天10 + 工具10 + 记忆5 + 异常5
python3 work/test_precision_v0251.py --only tool   # 只跑某一类
```

统计：普通聊天误触发工具次数、工具调用成功次数、非法 JSON 次数、重试次数、
file 误用次数、memory 误用次数、是否创建了无关文件（例如 `小米12.txt`）。

| 编号 | 操作 | 期望 |
| --- | --- | --- |
| A | 输入「你好」 | 文字流式输出（边生成边显示） |
| B | 输入「现在几点？」 | `[调用工具: time]`，最终回答同样流式输出 |
| C | 输入「查看当前目录」 | `[调用工具: shell]`，列出项目文件 |
| D | 输入「读取 README.md」 | `[调用工具: file]`，概括文件内容 |
| E | 退出后重新启动 | 之前的记忆被读入并出现在 system prompt 中 |
| F | 删除或破坏 `memory.json` | 程序正常启动：自动创建或忽略损坏内容 |
| G | 关闭模型 server | 给出人话错误提示，不出现 traceback |
| H | 非 Termux 环境 | Termux 检测返回 no，不影响任何功能 |

v0.25 的工具协议专项测试：

| 编号 | 操作 | 期望 |
| --- | --- | --- |
| 1 | 输入「现在几点？」 | 模型输出 tool_call → 调用 `time` → 返回答案 |
| 2 | 输入「查看当前目录」 | 调用 `shell` |
| 3 | 输入「读取 README.md」 | 调用 `file` |
| 4 | 输入「你好」 | 不调用工具，直接回答 |
| 5 | 模型请求不存在的工具 `abc` | 友好提示「没有名为 abc 的工具」，不崩溃 |
| 6 | 模型调用 `shell` 但不给 `command` | 返回「缺少必填参数：command」 |

协议本身还有一组解析测试（`tool_parser.parse_tool_call()`）：

- 16 种「带噪声但合法」的输出（代码块、前后解释文字、大小写、多余字段、字符串里含大括号…）全部解析成功；
- 14 种非法输出（旧协议、缺字段、类型错、截断 JSON、单引号、多余逗号、纯文字…）全部被拒绝。

快速自测命令：

```bash
printf '你好\n现在几点？\n查看当前目录\n读取 README.md\nexit\n' | python main.py
```

## 已知限制

- 流式只做到文本增量显示，不显示 token 速度、不做打字机特效
- 记忆是「问答日志」形式，没有摘要、没有向量检索、没有语义去重
- 一轮只调用一个工具，没有并行工具调用与任务分解
- 会话上下文不落盘，重启后是新的会话（记忆除外）
- Termux 唤醒锁与通知已实现但未默认启用（需要 `pkg install termux-api`）
- 协议解析是「严格 + 三层容错」：全角标点、扁平参数、重试时用 JSON 约束解码；
  但真正残缺的 JSON（例如 `"arguments：{}` 少了引号）不会被本地修补，只能靠一次重试
- 旧协议默认不再兼容，需要 `tools.legacy_protocol: true` 才能过渡使用
- 一轮仍然只调用一个工具；参数校验只做必填项、未知参数与类型检查，不做语义层面的预校验
- 真机上 Qwen2.5-3B 仍会偶发输出「裸参数 JSON」或把工具参数写错（`{"action":"file","path":"."}`），
  v0.25.1 的容错能救回一部分，剩下的会被当成普通回答显示出来
- 聊天工具门控是关键词启发式：措辞完全不常见的工具请求可能被门控拦一次（多花一次模型调用，
  但不会给出错误结果）
- 手机长时间推理会热降频，实测同一条请求从 20s 变到 300s+

## 下一阶段建议（v0.3 候选，本轮未实现）

1. 记忆摘要：条目多了以后做一次轻量压缩，替代简单的「最近 N 条」
2. 多 profile：`config.yaml` 支持多套模型配置，一键在 1.5B / 3B / 4B 间切换
3. 工具白名单配置化：把 `ALLOWED_COMMANDS` 移到 `config.yaml`
4. 断流自动续写：流式中断后自动重试一次，只补生成缺失部分
5. Termux 体验：启动时可选 `wake_lock()`，长回答结束时发通知
6. 输出渲染：Markdown 折叠、代码块高亮（保持零依赖则只做最简着色）
