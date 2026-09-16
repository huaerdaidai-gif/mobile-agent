# Mobile Agent v0.26

面向手机 / Termux 的轻量 Agent Runtime：本地或 OpenAI 兼容 API 的模型 + 工具 + 记忆 + QQ 渠道。

## 项目定位

- 手机优先：低内存、低依赖、无常驻后台线程；
- 小模型友好：1B~4B 量化模型（默认 Qwen2.5-3B-Instruct Q4_K_M）；
- 本地优先：模型走 llama.cpp 的 OpenAI 兼容接口，默认 `127.0.0.1`；
- 有真实渠道：QQ（OneBot v11）接入，HTTP 与 CLI 同时可用。

不是大型 Agent Framework（无 LangChain / LlamaIndex / AutoGen），不是 SaaS，
不是多 Agent 平台，不需要数据库与容器。

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

```
普通文字：User ─▶ Agent ─▶ Primary Model ─▶ Response
工具：    User ─▶ Agent ─▶ Tool ─▶ Tool Result ─▶ Model ─▶ Response
```

Core 不依赖具体渠道 / 平台 / 模型实现：QQ 在 `adapter/qq/`，Termux 在 `adapter/platform/`，
Qwen / llama.cpp 在 `model/` + `adapter/model/`，HTTP 由 `gateway/`（纯标准库）承担。

## 本版本（Stage 1–3）新增

### 1. 模型层

| 角色 | 状态 | 说明 |
| --- | --- | --- |
| primary | READY | Qwen2.5-3B，唯一常驻模型，纯文本 |
| vision | UNAVAILABLE | 未部署；`/props.modalities.vision=false` |
| audio | UNAVAILABLE | 未部署；`/props.modalities.audio=false` |

- `ModelRegistry`：能力 = `config.models.<role>` 显式配置 + llama.cpp `/props.modalities` 真检测，
  两者都确认才算可用，**不伪装支持**；
- `LightweightModelRouter`：确定性规则、零 token、不调用第二个 LLM。

| 输入 | 路由 | steps |
| --- | --- | --- |
| 纯文本 | TEXT | `[primary]` |
| 需要视觉但不可用 | TEXT + 降级说明 | `[primary]`，本轮提示里写明「视觉模型不可用」 |
| 需要音频但不可用 | TEXT + 降级说明 | `[primary]`，同上 |

### 2. 工具层（分组 + 按需 schema）

14 个工具、7 个组，定义集中在 `tools/registry.py`：

| 组 | 工具 | 默认 | 状态 |
| --- | --- | --- | --- |
| core | `time` | on | AVAILABLE |
| system | `file` / `shell` | on | AVAILABLE（路径限制、命令白名单） |
| web | `weather` / `web_search` / `web_fetch` | on | AVAILABLE |
| writing | `document_write` | on | AVAILABLE |
| vision | `image_info` / `image_download` | off | 组关闭；打开后可用（`image_analyze` 需要视觉模型 → UNAVAILABLE） |
| audio | `speech_to_text` / `text_to_speech` | off | **UNAVAILABLE**（无音频模型） |
| media | `music_search` / `music_play` | off | **UNAVAILABLE**（无可靠 Provider） |

- system prompt 固定不变（基础提示词 + 协议 + 记忆块），工具 schema 按需贴到当前 user 消息，
  切组只改尾部，保住 prompt cache；
- Tool Gate 用关键词 + 保守规则（不用 LLM），泛化词不触发工具；
- 工具协议仍是 v0.25 的统一协议：`{"type": "tool_call", "name": ..., "arguments": {...}}`。

### 3. 调度层

`runtime/scheduler.py`：`sequential`（默认，模型调用串行）、I/O 并行（用于 `/health` 三探针）、
`mixed`；`parallel_models=false`、`max_active_models=1`。

### 4. 分级计时

每次请求记录 `total_ms / route_ms / gate_ms / schema_ms / model_ttft_ms / model_generation_ms /
tool_ms / first_chunk_ms / model_calls / tool_calls`，生产日志只打 total/ttft/model/tool。

## 核心能力

- OpenAI 兼容 Model Provider（`requests` 缺失时回退标准库 `urllib`，流式同样可用）
- Model Registry / Model Router / capability detection
- Tool Registry + Tool Groups + Schema Cache
- Context 预算裁剪：`实际预算 = min(agent.max_context, 服务器 n_ctx)`
- Memory：启动回灌 + 每轮写入，「记住」类请求直接写盘（0 次模型调用）
- Scheduler / 分级计时 / Streaming（SSE）
- QQ（OneBot v11）：私聊、群聊、@ 门槛、白名单、文字、长文分片
- Web（weather / web_search / web_fetch）、File、Shell、Document Write
- Vision / Audio：独立 provider 扩展点（均未部署，状态 UNAVAILABLE）

## 配置

`config.yaml` 为本地配置，`config.example.yaml` 是公开示例（含全部字段注释）。
敏感信息用环境变量占位（如 `${MOBILE_AGENT_QQ_TOKEN}`、`${MOBILE_AGENT_GATEWAY_TOKEN}`），
默认只监听 `127.0.0.1`。

新增配置段：`models.primary/vision/audio`、`scheduler.*`、`tools.<组>.enabled`、
`performance.web_*`，并把 `tool_result_max_chars` 收紧到 1000、`memory_max_items` 到 3、
`max_output_tokens` 到 256。

## 安全

- token / 密钥只走环境变量，仓库不写真实值；
- `/admin/*` 默认拦截（`extension.admin_enabled: false`），打开也要先过认证；
- QQ 事件推送可校验 `qq.push_token`，调用 OneBot API 用 `qq.token`；
- 运行产物（`runtime/state/`、`*.bak*`）不在版本控制内。

## 安装与运行

```bash
git clone <你的仓库地址> mobile-agent
cd mobile-agent
cp config.example.yaml config.yaml
python3 main.py
```

Termux 一键安装：`bash scripts/install-termux.sh --auto-deps`，之后用
`mobile-agent start|stop|status|test|health|chat|config`。

模型服务：

```bash
llama-server -m qwen2.5-3b-instruct-q4_k_m.gguf --host 127.0.0.1 --port 8080 --ctx-size 2048
```

`requests` 与 `PyYAML` 都是可选依赖；不装也能运行。

## 测试

```bash
python3 -m unittest discover -s tests
```

全部为离线测试（假模型 / 假 OneBot / 假 HTTP），v0.26 的 test 套件共 **147 tests**。

## 版本历史

| 版本 | 能力 | 测试 |
| --- | --- | --- |
| v0.1 | 工具调用循环、JSON 工具协议、上下文裁剪、记忆写入、Shell 白名单、路径限制 | — |
| v0.2 | 记忆回灌、流式输出（SSE）、Termux 检测、`/status` | — |
| v0.25 | 统一工具协议、工具注册表、严格解析器、统一 `{ok, result}` 返回 | — |
| v0.25.1 | 上下文自动探测、prompt 预算、记住意图直写、聊天工具门控、JSON 约束重试、全角标点归一化 | 72 |
| v0.26 | Model Registry / Router、capability detection、Tool Groups + Schema Cache、Web tools、Vision/Audio 扩展架构、Scheduler、分级计时 | 147 |

测试数为对应 Git commit 的离线测试实测值（`python3 -m unittest discover -s tests`）。

## 已知限制

- 手机内存是硬约束：llama-server 常驻后可用内存有限，单 slot 时模型并行没有意义；
- Vision / Audio 只有扩展点与配置位，没有部署模型，`image_analyze` 与语音工具都是 UNAVAILABLE；
- 图片目前只能做元信息与下载，**尚未**接入视觉理解链路；
- QQ 目前只处理文字消息（图片消息会被忽略）；
- 记忆是问答日志形式，没有摘要与检索；会话上下文不落盘；
- 一轮只调用一个工具，没有并行工具调用与任务拆分；
- 手机上长时间推理会热降频。
