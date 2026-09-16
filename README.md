# Mobile Agent v0.27

面向手机 / Termux 的轻量 Agent Runtime：用 Python 标准库把「本地或 OpenAI 兼容 API 的模型
+ 工具 + 记忆 + QQ 渠道 + 多模态扩展点」串成一个可以长期挂在手机上的进程。

## 项目定位

Mobile Agent 是给**手机（Termux）**用的 Agent Runtime，核心目标是：

- 手机上能跑：低内存、低依赖、无常驻后台线程；
- 小模型友好：1B~4B 量化模型（默认 Qwen2.5-3B-Instruct Q4_K_M）也能较稳定地调用工具；
- 本地优先：模型走 llama.cpp 的 OpenAI 兼容接口（`POST /v1/chat/completions`），默认 `127.0.0.1`；
- 有真实渠道：QQ（OneBot v11）是第一个真实接入的聊天渠道，HTTP 与 CLI 同时可用；
- 可扩展：Vision / Audio 是独立 provider 扩展点，不塞进主模型、默认不启用。

它**不是**：

- 不是大型 Agent Framework（没有 LangChain / LlamaIndex / AutoGen）；
- 不是云端 SaaS，不需要服务器、容器、数据库、向量库；
- 不是多 Agent 平台：一轮请求只有一个主模型 + 一次工具决策；
- 不是机器人 / 汽车 / 眼镜等特定硬件项目。

## 核心架构

```
手机
│
├─ Channel
│   └─ QQ / OneBot
│
└─ Mobile Agent
    ├─ Core
    ├─ Context
    ├─ Memory
    ├─ Tool
    ├─ Model
    └─ Runtime
         └─ llama.cpp
```

三条真实链路（按输入类型分流，路由是纯规则、零 token）：

```
普通文字：
User ─▶ Agent ─▶ Primary Model ─▶ Response

图片：
Image ─▶ Vision Provider ─▶ Visual Context（≤400 字）─▶ Primary Model ─▶ Response

工具：
User ─▶ Agent ─▶ Tool ─▶ Tool Result ─▶ Model ─▶ Response
```

代码分层（Core 不依赖具体渠道 / 平台 / 模型实现）：

| Core 不依赖 | 由谁负责 |
| --- | --- |
| QQ | `adapter/qq/onebot.py`（GatewayAdapter） |
| Termux / Android / 机型 | `adapter/platform/` 的 Host 实现 |
| Qwen / llama.cpp | `model/` 抽象 + `adapter/model/openai_compatible.py` |
| HTTP 框架 | `gateway/`（纯标准库 `http.server`） |

## 核心能力

以下都是当前代码里真实存在的能力：

| 能力 | 说明 |
| --- | --- |
| Model Provider | OpenAI 兼容（`llm.py`；`requests` 缺失时自动回退标准库 `urllib`） |
| Model Registry | 按角色管理 `primary` / `vision` / `audio`，未部署的角色绝不伪装可用 |
| Model Router | 确定性规则、零 token、不调用第二个 LLM（`model/router.py`） |
| Capability detection | 能力 = `config.models.<role>` 显式配置 + llama.cpp `/props.modalities` 真检测 |
| Tool Registry | 14 个工具 / 7 个组，名字、用途、参数、示例集中定义（`tools/registry.py`） |
| Tool Groups | 按提问只注入本轮需要的组；schema 贴在当前 user 消息，不动 system prefix |
| Schema Cache | 工具 schema 文本缓存，避免每轮重建 |
| Context | 预算裁剪：`实际预算 = min(agent.max_context, 服务器 n_ctx)`，历史超预算从最旧丢 |
| Memory | 启动回灌最近 N 条进 system prompt；「记住」类请求直接写盘（0 次模型调用） |
| Scheduler | `sequential`（默认）/ I/O 并行 / mixed；`parallel_models=false` |
| Streaming | SSE 流式输出，`requests` 与标准库两条路径都支持 |
| QQ / OneBot | 私聊、群聊、@ 门槛、白名单、文字、图片、长文分片 |
| Web Search / Fetch / Weather | 标准库实现；网页内容标记为 `[UNTRUSTED_CONTENT]` |
| File / Shell / Document Write | 路径限制在项目目录内；shell 白名单、不使用 `shell=True` |
| Vision pipeline | 独立 Vision provider → 短视觉上下文 → Primary（默认未部署视觉模型） |
| Audio extension point | provider / router / tool 占位接口，未部署音频模型（UNAVAILABLE） |

## Vision

视觉理解由**独立 provider** 承担，主模型不负责看图，也不接受图片输入：

```
image ─▶ Vision provider ─▶ visual context（≤400 字）─▶ Primary model ─▶ 回答
```

规则（实现于 `agent._vision_stage()` 与 `model/router.py`）：

- Vision 结果不进入 system prompt、不进入 history、不进入 memory；
- 只作为**当前这一轮**的临时上下文注入（形如 `（图片理解结果：…）`）；
- 普通文字消息不会触发 Vision：没有 `attachments["images"]` 时这段代码根本不执行；
- 视觉输出硬上限 400 字符，超出截断后标记；
- Vision 不可用（未配置 / `supports_vision=false` / 调用失败 / 返回空）时**明确降级**：
  告诉主模型「视觉不可用」，由主模型如实回复用户，绝不假装看过图片；
- 默认模型串行：`scheduler.parallel_models=false`、`max_active_models=1`；
- 多图片当前由 Agent 采用**第一张**策略（QQ 层不截断，收集到几张就传几张）。

QQ 图片链路：

```
OneBot image segment
   ─▶ attachments["images"]        （data.url 优先，data.file 兜底）
   ─▶ AgentService / Session / Agent
   ─▶ Model Router
   ─▶ Vision provider
   ─▶ visual context
   ─▶ Primary model
```

纯图片消息（只有图、没有文字）会补一个默认问题：`请描述这张图片。`

状态说明：Vision 链路（含 QQ 图片 attachments 透传）已完成并覆盖离线测试，
但**尚未部署真实视觉模型**，也**尚未完成手机实机验收**——当前 `models.vision.enabled=false`，
`image_analyze` 工具与 Vision 路由都如实报告 UNAVAILABLE。

## Audio

Audio 目前只有 **infrastructure / extension point**：

- `models.audio.*` 配置位、Model Registry 的 `audio` 角色、Router 的 AUDIO 路由规则已经留好；
- 工具 `speech_to_text` / `text_to_speech` 已注册但状态为 UNAVAILABLE（未部署音频模型）；
- 没有部署 STT / TTS 模型，也**没有**在手机或 QQ 上验证过语音。

当前状态就是：预留接口，等待接入模型后再验收。

## QQ

OneBot v11（NapCat / Lagrange 等实现）通过 HTTP 把事件推给网关：

```
QQ ─▶ OneBot ─▶ gateway /qq/onebot ─▶ QQ adapter ─▶ ChannelMessage ─▶ Agent ─▶ 回复发回 OneBot
```

当前真实能力：

- 私聊与群聊；群聊需要 @ 机器人才响应（`qq.require_at`），可用 `qq.reply_group` 关掉群聊；
- `qq.allow_users` 白名单；自己发的消息、非消息事件一律忽略；
- 文字消息行为与 v0.25.1 相同（同样的 session 划分、同样的回复与分片逻辑）；
- 图片消息：解析 OneBot `image` 段，形成 `attachments["images"]`，交给 Agent 的 Vision 链路；
  `data.url` 优先、`data.file` 兜底，两者都为空则跳过该段；
- 纯图片消息补默认问题 `请描述这张图片。`；多图不在 QQ 层截断；
- 长回复按 `qq.max_chars` 分片发送，群聊可选 `at_sender`。

说明：QQ 链路是用假 OneBot 实现（HTTP 服务端 mock）做的回归验证，
**尚未完成真实 QQ + 真实 Vision 的手机实机验收**。配置步骤见 `docs/QQ_SETUP.md`。

## Tools

当前 14 个工具、7 个组（`tools/registry.py` 是唯一事实来源）：

| 组 | 工具 | 默认 | 状态 |
| --- | --- | --- | --- |
| core | `time` | on | AVAILABLE |
| system | `file` | on | AVAILABLE（路径限制在项目目录内） |
| system | `shell` | on | AVAILABLE（命令白名单、15s 超时、无 `shell=True`） |
| web | `weather` | on | AVAILABLE（Open-Meteo，免 Key） |
| web | `web_search` | on | AVAILABLE（Bing 优先 / DuckDuckGo 兜底） |
| web | `web_fetch` | on | AVAILABLE（标准库清洗，硬上限 2500 字符） |
| writing | `document_write` | on | AVAILABLE（不额外调用模型） |
| vision | `image_info` | off | AVAILABLE（打开组即可用） |
| vision | `image_download` | off | AVAILABLE（打开组即可用，需要网络） |
| vision | `image_analyze` | off | **UNAVAILABLE**（需要视觉模型） |
| audio | `speech_to_text` | off | **UNAVAILABLE**（需要音频模型） |
| audio | `text_to_speech` | off | **UNAVAILABLE**（需要音频模型） |
| media | `music_search` | off | **UNAVAILABLE**（无可靠 Provider） |
| media | `music_play` | off | **UNAVAILABLE**（无可靠 Provider） |

工具调用协议（v0.25 统一协议，v0.27 未变）：模型需要工具时只输出一个 JSON 对象：

```json
{"type": "tool_call", "name": "shell", "arguments": {"command": "ls"}}
```

解析由 `tool_parser.py` 负责（纯 JSON / 代码块 / 前后带少量解释文字），并严格校验
`type`、`name`、`arguments`，不猜字段。结果统一回灌成
`工具结果：{"ok": true, "result": "..."}`（失败为 `{"ok": false, "error": "..."}`）。
旧协议 `{"tool": "shell", "command": "ls"}` 默认废弃，需要时可开 `tools.legacy_protocol`。

## 性能原则

架构层面的取舍如下（不是性能保证）：

1. 普通聊天走最短路径：Agent → Primary，不经过 Vision / Audio / Tool；
2. Tool Schema 按需注入：只有当前轮真的需要某个工具组时才贴 schema；
3. Schema Cache：schema 文本只构建一次，之后复用；
4. I/O 可并行：纯网络探测（如 `/health` 的三项探针）用一次性线程池；
5. 模型默认串行：单 slot 服务器下并行只会排队，`parallel_models=false`；
6. Tool Result 限长：默认 ≤1000 字符，超长按 JSON 结构安全截断；
7. Memory 小型化：回灌条数与占比都有上限（默认 3 条、≤实际上下文 10%）；
8. Streaming：回答边生成边显示，工具调用过程只打印一行状态；
9. 无永久后台线程；I/O 并行使用一次性线程池；模型默认串行。

在小米12 上的观测值（仅供参照，不作为承诺）：热缓存下普通聊天 total ≈4.1s、TTFT ≈1.34s；
经 QQ 实链路 ≈7.1s；Agent 空闲 RSS ≈11MB；llama-server 常驻约 0.9–1.9GB（随分页波动）。

## 配置

配置文件为 `config.yaml`，示例为 `config.example.yaml`（字段含义写在示例注释里）。

- 敏感信息一律用环境变量占位，例如 `${MOBILE_AGENT_QQ_TOKEN}`、`${MOBILE_AGENT_GATEWAY_TOKEN}`；
- 默认只监听本机：`llm.base_url: http://127.0.0.1:8080/v1`、`gateway.host: 127.0.0.1`；
- 不依赖 PyYAML：没装 PyYAML 时使用内置的极简解析（支持多层结构）。

主要开关（默认值）：`stream: true`；`agent.max_context: 4096`（实际取与服务器 `n_ctx` 的较小值）、
`agent.max_steps: 4`；`performance.tool_result_max_chars: 1000`、`max_output_tokens: 256`、
`memory_max_items: 3`、`memory_max_ratio: 0.10`、`system_prompt_max_tokens: 300`、
`chat_tool_gate: true`；`models.primary.enabled: true`、`models.vision/audio.enabled: false`；
`scheduler.parallel_models: false`、`max_active_models: 1`；`tools.<组>.enabled`；
`gateway.auth: token` 或 `none`；`qq.enabled`、`qq.api_base`、`qq.require_at`、`qq.allow_users`。

## 安全

- 配置只出现占位符：token / 密钥一律走环境变量，仓库里不写真实值；
- OneBot 事件推送可校验 `qq.push_token`（OneBot → 网关方向），调用 OneBot API 用 `qq.token`（网关 → OneBot 方向）；
- 高风险接口 `/admin/*` 默认拦截：`extension.admin_enabled: false`，即使打开也要先过认证；
- 未来所有远程控制按 `Authentication → Authorization → Policy → Executor` 链路设计；
- 本地运行产物（`runtime/state/`、`*.bak*`、设备笔记等）已在 `.gitignore` 中，不入库；
- 本地备份分支只作回滚用，不参与发布，也不推送。

## 安装与运行

```bash
git clone <你的仓库地址> mobile-agent
cd mobile-agent
```

方式一：直接跑 CLI（不装任何第三方依赖也能用）

```bash
cp config.example.yaml config.yaml   # 按需修改模型地址、QQ 配置
python3 main.py
```

方式二：Termux 一键安装（把 `cli.py` 链接成 `mobile-agent` 命令）

```bash
bash scripts/install-termux.sh --auto-deps
# 需要唤醒锁 / 通知 / 电池温度时再加：--with-termux-api
mobile-agent start
mobile-agent status      # 期望看到 Host / Model / Gateway 状态
mobile-agent test        # 离线测试 + 模型连通性检查
```

启动模型（llama.cpp，OpenAI 兼容接口）：

```bash
llama-server -m qwen2.5-3b-instruct-q4_k_m.gguf --host 127.0.0.1 --port 8080 --ctx-size 2048
```

`requests` 与 `PyYAML` 都是可选依赖（`pip install -r requirements.txt`）：
没有它们时 HTTP 走标准库 `urllib`（流式同样可用），配置走内置解析器。

CLI 命令：`exit` / `quit` / `退出` 退出，`/status` 看运行状态（不显示任何密钥），
`/clear` 清空本次会话上下文，`/tools` 查看工具清单（按组、标注可用性）。

## 测试

```bash
python3 -m unittest discover -s tests
```

全部为离线测试：假模型服务、假 OneBot 服务、假 HTTP 客户端，不依赖真机、不访问外网。

| 版本 | 对应 Git 状态 | 离线测试数（实测） |
| --- | --- | --- |
| v0.25.1 | tag `v0.25.1` | 72 |
| v0.26 | tag `v0.26` | 147 |
| v0.27 | tag `v0.27`（当前） | 199 |

版本行的数字是在对应 commit 上实际跑 `python3 -m unittest discover -s tests` 得到的，
不是开发过程里的中间值。

## 版本历史

### v0.25.1

- Agent Core：工具循环、上下文预算、流式闸门、历史裁剪；
- Context：启动探测服务器真实上下文，实际预算取 `min(配置, 服务器)`；
- Memory：启动回灌 + 每轮写入，记忆文件损坏不崩溃；
- Tool Loop 与统一工具协议 `{"type": "tool_call", "name": ..., "arguments": {...}}`；
- 严格解析器 + 三层容错（全角标点归一化、扁平参数、JSON 约束重试）；
- Streaming（SSE）、Termux 兼容层、OpenAI 兼容模型接口（llama.cpp）；
- QQ（OneBot）适配器与 Gateway / CLI / Host 骨架；
- 149 tests。

### v0.26

- Model Registry / Model Router（确定性、零 token 路由）；
- capability detection（配置 + llama.cpp `/props.modalities` 真检测）；
- Tool Groups 与 Schema Cache，system prompt 固定以保住 prompt cache；
- Web tools（weather / web_search / web_fetch）与 web 结果长度限制；
- Vision / Audio 扩展架构（独立 provider 与路由规则，默认 UNAVAILABLE）；
- Scheduler（sequential / I/O parallel / mixed，`parallel_models=false`）；
- 分级计时结构（total / route / schema / ttft / model / tool）；
- 169 tests。

### v0.27

- Vision attachment pipeline：`attachments → Router → Vision provider → ≤400 字 visual context → Primary`；
- 图片不进入 history / memory / system prompt，只作用于当前轮；
- Vision 不可用时明确降级，不伪造视觉结果；
- QQ image attachments：`data.url` 优先、`data.file` 兜底、多图不截断；
- 纯图片消息默认问题 `请描述这张图片。`；
- QQ → attachments → Agent → Vision 的透传链路与回归测试；
- 199 tests。

## 已知限制

- 手机内存是硬约束：主模型常驻后，再加载第二个视觉模型会明显吃紧，单 slot 时两个模型不能同时推理；
- 多图片只处理第一张；
- Vision 仅完成架构与离线测试，尚未在手机上完成实机验收；视觉模型未部署；
- Audio 只有接口，没有模型，也没有任何实机验证；
- llama.cpp 单 slot 时模型并行没有意义，`parallel_models` 默认关闭；
- 记忆是问答日志形式，没有摘要、没有向量检索、没有语义去重；
- 会话上下文不落盘，重启后是新会话（记忆除外）；
- 一轮只调用一个工具，没有并行工具调用与任务拆分；
- 聊天工具门控是关键词启发式：措辞生僻的工具请求可能被拦一次（多花一次模型调用，但不会给错答案）；
- 手机上长时间推理会热降频；冷启动曾观测到数分钟级（约 566s），原因未定位，仅作观测记录。

## 目录结构

```
mobile-agent/
├── main.py              # CLI 入口（交互式聊天、/status、/tools）
├── cli.py               # mobile-agent start|stop|status|test|health|chat|config
├── agent.py             # Agent Core：工具循环、记忆、上下文预算、Vision stage
├── llm.py               # OpenAI 兼容 HTTP 层（chat / chat_stream / SSE）
├── tool_parser.py       # 工具协议解析与校验
├── config.yaml          # 本地配置（config.example.yaml 是公开示例）
├── gateway/             # HTTP 网关：/health、/api/chat[/stream]、/qq/onebot
├── runtime/             # service（组装）、session、scheduler、config、logging、state/
├── model/ + adapter/    # ModelProvider 抽象与 OpenAI 兼容实现、QQ、Host（Termux）
├── host/ capability/    # Host 抽象（资源快照）、Capability 注册
├── extension/           # LearningEngine / DeviceRegistry / ParameterTuner / Plugin 等接口（默认关闭）
├── tools/               # 分组工具：core / system / web / writing / vision / audio / media
├── platform/termux.py   # Termux 检测与 termux-api 调用（可选）
├── docs/QQ_SETUP.md     # QQ / OneBot 配置步骤
├── scripts/             # Termux 安装 / 启停 / 状态脚本
├── tests/               # 199 个离线测试
└── memory/memory.json   # 持久化记忆
```
