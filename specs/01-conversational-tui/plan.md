# 多协议 LLM 终端对话客户端 Plan

## 架构概览

系统按职责分为五层，依赖方向自上而下单向、无环：

- **入口/装配层（cli）**：定位并加载 YAML 配置，产出 `AppConfig`，构造并运行 TUI 应用。无任何影响行为的命令行 flag（对齐 spec「不做 flag 覆盖」）。
- **配置层（config）**：从 YAML 读取 providers 列表、逐项校验，产出内存配置对象。不解析环境变量、不落盘。
- **Provider 层（providers）**：与协议无关的统一抽象。定义 `Provider` 接口与统一增量事件 `StreamEvent`，Anthropic / OpenAI 各自把自家 SSE 归一化为统一事件；思考增量在此层被识别并丢弃，不外泄。工厂按 `protocol` 选实现。
- **会话层（conversation）**：单次会话的**内存**对话历史；把「内置 system prompt + 完整历史」组装为请求上下文。程序退出即消失，不持久化。
- **界面层（tui）**：基于 Textual 的全功能应用。含 provider 选择屏、启动 banner、对话区（流式纯文本 → 结束 markdown 美化）、响应计时指示、多行输入框、状态栏、错误样式展示。

上层对话循环只依赖 `Provider` 接口与 `StreamEvent`，不感知具体后端（满足 F3/N3）。全程 asyncio：Textual 事件循环驱动，`httpx.AsyncClient` 异步流式接收，`async for` 增量更新界面，界面不阻塞（N1）。

## 核心数据结构

### Role
消息角色枚举：`user`、`assistant`、`system`。

### Message
一条对话消息。字段：`role: Role`、`content: str`。

### StreamEventKind
统一增量事件类型枚举：`TEXT_DELTA`（正文增量）、`ERROR`（出错，含可读信息）、`DONE`（本轮结束）。
> 注意：**无 thinking 事件**。思考增量在 Provider 内被识别后直接丢弃，不进入事件流（对应 spec F5「接收即丢弃、不混入正文」）。

### StreamEvent
Provider 与上层之间的唯一数据契约。字段：`kind: StreamEventKind`、`text: str = ""`（`ERROR` 时为错误描述，`DONE` 时为空）。提供便捷构造：`text_delta(s)`、`error(msg)`、`done()`。

### ProviderConfig
单个供应商配置，对应 YAML 一个条目的六字段：
- `name: str` —— 可读名称
- `protocol: str` —— `anthropic` | `openai`
- `model: str`
- `base_url: str | None` —— 可选自定义端点，缺省用协议默认端点
- `api_key: str`
- `thinking: bool = False`

### AppConfig
整个应用配置。字段：`providers: list[ProviderConfig]`（保序，供选择屏按序展示）。提供 `single() -> ProviderConfig | None`（恰一个时返回它，否则 `None`）。**无 default 字段**（多个时靠交互选择）。

### Provider（接口）
统一抽象基类。构造接收 `ProviderConfig`；只读属性 `name`、`model`。核心方法：
- `stream(messages: list[Message]) -> AsyncIterator[StreamEvent]` —— 异步生成器。传入「已含 system prompt 的完整上下文」，向后端发流式请求，把 SSE 逐块归一化为 `StreamEvent` 并 `yield`；识别到思考增量则跳过不产出；网络/HTTP 错误在内部捕获转成 `error` 事件后正常收束（不抛、不含密钥）。

### Conversation
单次会话的内存历史。字段：`messages: list[Message]`、`system_prompt: str`。方法：
- `add_user(text)`、`add_assistant(text)` —— 追加消息
- `build_context() -> list[Message]` —— 返回「system 消息 + 全部历史」供 `Provider.stream`
无任何落盘/加载方法。

## 模块设计

### errors（`mewcode/errors.py`）
**职责：** 统一异常。**接口：** `MewCodeError`、`ConfigError`、`ProviderError`。**依赖：** 无。

### messages（`mewcode/messages.py`）
**职责：** 跨层共享数据结构。**接口：** `Role`、`Message`、`StreamEventKind`、`StreamEvent`。**依赖：** 无。

### prompts（`mewcode/prompts.py`）
**职责：** 提供内置 system prompt。**接口：** `SYSTEM_PROMPT: str` 常量（或 `build_system_prompt() -> str`），描述 MewCode 助手身份与风格。**依赖：** 无。

### config（`mewcode/config.py`）
**职责：** 定位配置文件、解析 YAML、校验，产出 `AppConfig`。**接口：** `load_config() -> AppConfig`、`ProviderConfig`、`AppConfig`。
**要点：**
- 查找顺序：`./mewcode.yaml` > `~/.config/mewcode/config.yaml`，取第一个存在者；都不存在抛 `ConfigError`（提示如何创建）。
- 校验：`providers` 非空；每条含必需字段（`name`/`protocol`/`model`/`api_key`）；`protocol ∈ {anthropic, openai}`；`base_url`、`thinking` 可选。任一非法抛 `ConfigError`（可读、不含密钥）。
- **不解析环境变量、不接受 flag 覆盖。**
**依赖：** PyYAML、`errors`。

### providers（`mewcode/providers/`）
**职责：** Provider 统一抽象与两后端实现，把各家 SSE 归一化为 `StreamEvent`。
**接口：** `base.py` 的 `Provider`；`factory.py` 的 `create_provider(cfg) -> Provider`；`anthropic.py`、`openai.py`；`sse.py` 共享 SSE 逐行解析；`__init__.py` 导出 `Provider`、`create_provider`。
**要点：**
- `AnthropicProvider`：`POST {base_url|默认}/v1/messages`，头 `x-api-key`、`anthropic-version`；体含 `model`、`max_tokens`（普通 4096，`thinking` 启用时提到 8192）、`system`（system prompt 文本）、`messages`（非 system 历史）、`stream=True`；`thinking` 启用时加 `thinking={"type":"enabled","budget_tokens":2048}`。解析 `content_block_delta`：`delta.type=="text_delta"` → `text_delta`；`=="thinking_delta"` → **跳过丢弃**；`message_stop` → `done`。
- `OpenAIProvider`：`POST {base_url|默认}/v1/chat/completions`，头 `Authorization: Bearer`；体含 `model`、`messages`（system 作为首条 `{role:"system"}`）、`stream=True`；忽略 `thinking`。解析 `choices[0].delta.content` → `text_delta`；`data:[DONE]` → `done`。
- 错误：非 2xx / 网络异常 → `error` 事件（可读、不含密钥）后收束。
**依赖：** httpx、`messages`、`config`、`prompts`（system 由 conversation 注入，Provider 只透传）、`errors`。

### conversation（`mewcode/conversation.py`）
**职责：** 内存对话历史与上下文组装。**接口：** `Conversation(system_prompt)`、`add_user`、`add_assistant`、`build_context`。**依赖：** `messages`。

### tui（`mewcode/tui/`）
**职责：** 全功能界面与对话编排。
**接口/组成：**
- `app.py`：`MewCodeApp(App)`，持有 `AppConfig`、活动 `Provider`、`Conversation`。
- `screens.py`：`ProviderSelectScreen`（方向键 `ListView`，展示各 provider 名称+模型，回车选定）。
- `widgets.py`：`Banner`（ASCII 猫咪 + 应用名/版本 + cwd）、就绪提示行、`UserMessage`/`AssistantMessage`/`ErrorMessage` 组件、`ThinkingIndicator`（`Imagining… (Ns)` 计时）、多行 `PromptInput`（`❯` + 占位符）、状态栏（左 provider 名 / 右 model）。
**要点：**
- 启动：`on_mount` 渲染 banner + 就绪行 + 状态栏；`AppConfig.single()` 有值则直接用它 `create_provider` 进入对话；否则 `push_screen(ProviderSelectScreen)`，选定回调里 `create_provider` 并更新状态栏。
- 提交流程（async）：输入非空且非 `/exit` → `conversation.add_user(text)` 并渲染用户消息 → 锁定输入 → 新建 `AssistantMessage` + 启动 `ThinkingIndicator`（`set_interval` 每秒刷新秒数，记录起始时间）→ `async for ev in provider.stream(conversation.build_context())`：`TEXT_DELTA`→（首个到达时保留计时、继续）逐字 append 到该消息纯文本区、`ERROR`→渲染 `ErrorMessage`、`DONE`→停止计时、把整段正文用 markdown 重渲染定型、显示总耗时 → `conversation.add_assistant(正文)` → 解锁输入。
- `/exit` 输入或 Ctrl+C → 退出（Textual 自动恢复终端状态，N7）；多行编辑：`PromptInput` 拦截按键，Alt+Enter 插入换行、Enter 提交；提交后清空。
- markdown 与布局随终端宽度自适应（N6，靠 Textual/Markdown widget）。
**依赖：** Textual、`providers`、`conversation`、`messages`、`mewcode.__version__`。

### cli / 入口（`mewcode/cli.py`、`mewcode/__main__.py`）
**职责：** 装配与启动。**接口：** `main() -> int`；`__main__.py` 调用之。
**要点：** `load_config()` → `MewCodeApp(app_config).run()` → 返回 0；顶层捕获 `MewCodeError` 打印到 stderr 返回非零。无行为性 flag（仅隐含 `--help`）。
**依赖：** `config`、`tui`、`errors`。

## 模块交互

```
cli.main
  ├─ config.load_config()                    → AppConfig(providers[])
  └─ tui.MewCodeApp(app_config).run()
        on_mount: 渲染 banner/就绪行/状态栏
        provider 选择:
          single()  → create_provider(cfg)          （直接进对话）
          多个      → ProviderSelectScreen 选定 → create_provider(cfg)
        用户提交一条消息:
          ├─ conversation.add_user(text)             （渲染 UserMessage）
          ├─ 启动 ThinkingIndicator（Imagining… (Ns)）
          ├─ async for ev in provider.stream(conversation.build_context()):
          │      TEXT_DELTA → 逐字 append 到 AssistantMessage 纯文本
          │      ERROR      → 渲染 ErrorMessage（不退出）
          │      DONE       → 停止计时、markdown 重渲染定型、显示总耗时
          ├─ conversation.add_assistant(正文)
          └─ 解锁输入
        /exit 或 Ctrl+C → 退出并恢复终端
```

数据流：`AppConfig`（config→App）；选定 `ProviderConfig`（App→factory→Provider）；`list[Message]`（conversation.build_context→Provider.stream）；`StreamEvent` 流（Provider→TUI）。上层只见 `Provider` 接口与 `StreamEvent`，后端可插拔。

## 文件组织

```
Mewcode_Agent/
├── pyproject.toml                 — 依赖(textual, httpx, pyyaml)、dev(pytest, pytest-asyncio, respx)、Python≥3.10、入口脚本
├── mewcode.yaml.example           — 配置示例（占位符密钥，不含真实值）
├── .gitignore                     — 忽略 mewcode.yaml、__pycache__ 等
└── mewcode/
    ├── __init__.py                — __version__
    ├── __main__.py                — python -m mewcode 入口
    ├── cli.py                     — load_config + 启动 App
    ├── config.py                  — ProviderConfig/AppConfig/load_config（无 env/flag）
    ├── errors.py                  — MewCodeError/ConfigError/ProviderError
    ├── messages.py                — Role/Message/StreamEventKind/StreamEvent
    ├── prompts.py                 — 内置 system prompt
    ├── conversation.py            — Conversation（内存历史 + 上下文组装）
    ├── providers/
    │   ├── __init__.py            — 导出 Provider、create_provider
    │   ├── base.py                — Provider 抽象基类
    │   ├── factory.py             — create_provider(按 protocol 分派)
    │   ├── sse.py                 — 共享 SSE 逐行解析
    │   ├── anthropic.py           — AnthropicProvider（识别并丢弃 thinking）
    │   └── openai.py              — OpenAIProvider
    └── tui/
        ├── __init__.py
        ├── app.py                 — MewCodeApp（编排、计时、markdown 定型）
        ├── screens.py            — ProviderSelectScreen
        └── widgets.py            — Banner/消息组件/计时指示/输入框/状态栏
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| TUI 框架 | Textual（全功能） | 异步契合流式；内置 ListView（选择屏）、Markdown（美化）、set_interval（计时）、自动终端恢复（N6/N7） |
| HTTP/流式 | httpx.AsyncClient 手写 SSE | 两家皆 SSE，抽象层只需归一化为统一事件，最纯粹；单依赖、可控、base_url 灵活、契合 asyncio |
| 统一事件 | StreamEvent(text_delta/error/done)，无 thinking | 思考增量在 Provider 内识别并丢弃，不外泄；新增后端只需产出该事件（F3/N3/F5） |
| 错误处理 | Provider 内把网络/API 错误转 error 事件；TUI 以 ErrorMessage 展示不退出 | 流式过程不因异常中断应用，可继续下一轮（F11/N4） |
| 配置 | 纯 YAML，无 env 解析、无 flag 覆盖 | 对齐 spec「其它配置来源：不做」 |
| provider 选择 | 单个直接进；多个用 Textual 选择屏 | 对齐 spec F2；无 default/CLI 覆盖 |
| 历史 | 纯内存 Conversation，退出即消失 | 对齐 spec「不做会话持久化」 |
| system prompt | 内置常量，由 Conversation 组装进上下文 | 满足 F4；集中管理便于后续演进 |
| 输出渲染 | 流式期纯文本逐字，DONE 后整段 markdown 重渲染 | 满足 F8；流式实时性 + 结束美化两不误 |
| 等待反馈 | ThinkingIndicator + set_interval 每秒刷新秒数，DONE 显示总耗时 | 满足 F12/N2 |
| 输入 | 多行控件，Alt+Enter 换行、Enter 提交、流式期锁定 | 满足 F9 |
| thinking 请求 | Anthropic 启用时加 thinking 参数并提高 max_tokens；响应 thinking_delta 丢弃 | budget_tokens 须 < max_tokens；OpenAI 忽略该字段 |

> **边界注解（供后续阶段）：** 「thinking 丢弃/不回传」仅在本阶段**纯聊天、无工具调用**下成立。后续引入 tool use 后，**带工具调用的那一轮** thinking 块必须连同 `signature` 原样保留并与 `tool_result` 一并回传，否则 Anthropic API 报错。届时事件模型与历史需区分「是否带工具调用」两类轮次。
