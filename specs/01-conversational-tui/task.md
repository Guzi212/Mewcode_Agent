# 多协议 LLM 终端对话客户端 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `pyproject.toml` | 依赖(textual/httpx/pyyaml)、dev(pytest/pytest-asyncio/respx)、Python≥3.10、入口脚本 |
| 新建 | `.gitignore` | 忽略 `mewcode.yaml`、`__pycache__` 等 |
| 新建 | `mewcode.yaml.example` | 配置示例（占位符密钥） |
| 新建 | `mewcode/__init__.py` | `__version__` |
| 新建 | `mewcode/errors.py` | `MewCodeError`/`ConfigError`/`ProviderError` |
| 新建 | `mewcode/messages.py` | `Role`/`Message`/`StreamEventKind`/`StreamEvent` |
| 新建 | `mewcode/prompts.py` | 内置 system prompt |
| 新建 | `mewcode/config.py` | `ProviderConfig`/`AppConfig`/`load_config` |
| 新建 | `mewcode/conversation.py` | `Conversation`（内存历史 + 上下文组装） |
| 新建 | `mewcode/providers/{__init__,base,sse,anthropic,openai,factory}.py` | Provider 抽象与两后端 + 工厂 |
| 新建 | `mewcode/tui/{__init__,widgets,screens,app}.py` | 界面组件、选择屏、主应用 |
| 新建 | `mewcode/cli.py`、`mewcode/__main__.py` | 装配与入口 |
| 新建 | `tests/` 下若干 | 单元/冒烟测试 |

---

## T1: 项目骨架与依赖

**文件：** `pyproject.toml`、`.gitignore`、`mewcode.yaml.example`、`mewcode/__init__.py`、`mewcode/providers/__init__.py`、`mewcode/tui/__init__.py`、`tests/__init__.py`
**依赖：** 无
**步骤：**
1. `pyproject.toml`：`[project]` 名 `mewcode`、`requires-python=">=3.10"`、依赖 `textual`、`httpx`、`pyyaml`；`optional-dependencies.dev`：`pytest`、`pytest-asyncio`、`respx`；`[project.scripts]` `mewcode = "mewcode.cli:main"`；pytest 配 `asyncio_mode="auto"`。
2. `.gitignore`：`mewcode.yaml`、`__pycache__/`、`*.pyc`、`.venv/`、`*.egg-info/`。
3. `mewcode.yaml.example`：`providers:` 列两条示例（anthropic 那条 `thinking: true`、openai 那条含自定义 `base_url` 注释示例），`api_key` 用占位符字符串。**无顶层 default**。
4. `mewcode/__init__.py`：定义 `__version__ = "0.1.0"`。
5. 建空 `__init__.py`：`mewcode/providers/`、`mewcode/tui/`、`tests/`。
**验证：** `pip install -e ".[dev]"` 成功；`python -c "import mewcode; print(mewcode.__version__)"` 输出版本。

## T2: 统一异常类型

**文件：** `mewcode/errors.py`
**依赖：** 无
**步骤：**
1. `class MewCodeError(Exception)` 基类。
2. `class ConfigError(MewCodeError)`、`class ProviderError(MewCodeError)`。
**验证：** `python -c "from mewcode.errors import ConfigError; from mewcode.errors import MewCodeError; assert issubclass(ConfigError, MewCodeError)"` 无报错。

## T3: 共享数据结构

**文件：** `mewcode/messages.py`
**依赖：** 无
**步骤：**
1. `class Role(str, Enum)`：`USER`/`ASSISTANT`/`SYSTEM`。
2. `@dataclass Message`：`role: Role`、`content: str`。
3. `class StreamEventKind(str, Enum)`：`TEXT_DELTA`、`ERROR`、`DONE`（**无 thinking**）。
4. `@dataclass StreamEvent`：`kind`、`text: str = ""`；便捷构造 `text_delta(s)`、`error(msg)`、`done()`。
**验证：** `python -c "from mewcode.messages import StreamEvent, StreamEventKind; e=StreamEvent.text_delta('hi'); assert e.kind==StreamEventKind.TEXT_DELTA and e.text=='hi'"` 无报错。

## T4: 内置 system prompt

**文件：** `mewcode/prompts.py`
**依赖：** 无
**步骤：**
1. 定义 `SYSTEM_PROMPT: str`：描述 MewCode 助手身份（Claude Code 风格终端编程助手）、中文回答倾向、简洁风格。
**验证：** `python -c "from mewcode.prompts import SYSTEM_PROMPT; assert isinstance(SYSTEM_PROMPT, str) and SYSTEM_PROMPT"` 无报错。

## T5: 配置加载与校验

**文件：** `mewcode/config.py`
**依赖：** T2
**步骤：**
1. `@dataclass ProviderConfig`：`name`、`protocol`、`model`、`api_key`、`base_url: str | None = None`、`thinking: bool = False`。
2. `@dataclass AppConfig`：`providers: list[ProviderConfig]`；方法 `single() -> ProviderConfig | None`（恰一个返回它，否则 `None`）。
3. `load_config() -> AppConfig`：按 `./mewcode.yaml` > `~/.config/mewcode/config.yaml` 查找取第一个存在者，均无则抛 `ConfigError`（提示创建路径）；`yaml.safe_load`；校验 `providers` 非空、每条含 `name`/`protocol`/`model`/`api_key`、`protocol ∈ {anthropic, openai}`；非法抛 `ConfigError`（不含密钥值）。**不解析环境变量、不接受 flag。**
**验证：** `tests/test_config.py`：写临时 yaml（两条 provider）→ `load_config`（可注入路径或 monkeypatch 查找）得到 `AppConfig`，`single()` 返回 `None`；单条时 `single()` 返回该项；缺 `api_key` 抛 `ConfigError`；`protocol` 非法抛 `ConfigError`。

## T6: 会话历史与上下文组装

**文件：** `mewcode/conversation.py`
**依赖：** T3, T4
**步骤：**
1. `class Conversation`：构造接收 `system_prompt: str`，内部 `messages: list[Message] = []`。
2. `add_user(text)`、`add_assistant(text)` 追加对应角色 `Message`。
3. `build_context() -> list[Message]`：返回 `[Message(SYSTEM, system_prompt)] + messages`。
**验证：** `tests/test_conversation.py`：`add_user("a")`、`add_assistant("b")` 后 `build_context()` 首条是 SYSTEM，其后依次 user/assistant，内容正确。

## T7: Provider 抽象基类

**文件：** `mewcode/providers/base.py`
**依赖：** T3
**步骤：**
1. `class Provider(ABC)`：构造接收 `ProviderConfig` 存 `self._cfg`；只读属性 `name`、`model`。
2. `@abstractmethod async def stream(self, messages: list[Message]) -> AsyncIterator[StreamEvent]`。
3. 约定：错误转 `StreamEvent.error(...)` 后正常结束，不外抛；思考增量识别后丢弃、不产出事件。
**验证：** `python -c "from mewcode.providers.base import Provider"` 无报错；实例化 `Provider(...)` 抛 `TypeError`（抽象类）。

## T8: 共享 SSE 解析

**文件：** `mewcode/providers/sse.py`
**依赖：** 无
**步骤：**
1. `async def iter_sse(response) -> AsyncIterator[str]`：对 `response.aiter_lines()` 逐行，取 `data: ` 前缀后负载并 `yield`，跳过空行与非 data 行。
2. 由调用方判断 `[DONE]` 哨兵与 JSON 解析。
**验证：** `tests/test_sse.py`：用产出预设行的假响应喂 `iter_sse`，断言只提取 data 负载、正确跳过空行/非 data 行。

## T9: Anthropic Provider（丢弃 thinking）

**文件：** `mewcode/providers/anthropic.py`
**依赖：** T5, T7, T8
**步骤：**
1. `class AnthropicProvider(Provider)`；默认端点常量（`base_url` 为空时使用）。
2. 请求体：`model`、`max_tokens`（普通 4096，`thinking` 启用时 8192）、`system`（取历史中 SYSTEM 消息文本）、`messages`（非 system 历史转 `{role,content}`）、`stream=True`；`thinking` 启用时加 `thinking={"type":"enabled","budget_tokens":2048}`。头 `x-api-key`、`anthropic-version`、`content-type`。
3. `stream`：`httpx.AsyncClient` 对 `{base_url|默认}/v1/messages` 流式 POST；`iter_sse` 逐条 JSON：`content_block_delta` 中 `delta.type=="text_delta"` → `yield text_delta`，`=="thinking_delta"` → **跳过**；`message_stop` → `yield done`。
4. 非 2xx/网络异常 → `yield error(可读信息，不含密钥)` 后结束。
**验证：** `tests/test_anthropic.py`：respx mock `/v1/messages` 返回含 thinking_delta + 两个 text_delta + message_stop 的 SSE；收集事件断言：**无任何事件携带思考文本**、依次得到两个 text_delta、一个 done。

## T10: OpenAI Provider

**文件：** `mewcode/providers/openai.py`
**依赖：** T5, T7, T8
**步骤：**
1. `class OpenAIProvider(Provider)`；默认端点常量。
2. 请求体：`model`、`messages`（SYSTEM 消息作为首条 `{role:"system"}`，其余照转）、`stream=True`；忽略 `thinking`。头 `Authorization: Bearer`。
3. `stream`：对 `{base_url|默认}/v1/chat/completions` 流式 POST；`iter_sse`：`[DONE]` → `yield done`；否则 JSON 取 `choices[0].delta.content` 非空 → `yield text_delta`。
4. 错误处理同 T9。
**验证：** `tests/test_openai.py`：respx mock 返回含两段 `delta.content` + `[DONE]` 的 SSE；断言依次两个 text_delta、一个 done。

## T11: Provider 工厂

**文件：** `mewcode/providers/factory.py`、`mewcode/providers/__init__.py`
**依赖：** T9, T10
**步骤：**
1. `create_provider(cfg) -> Provider`：`anthropic`→`AnthropicProvider`、`openai`→`OpenAIProvider`、否则抛 `ProviderError`。
2. `__init__.py` 导出 `Provider`、`create_provider`。
**验证：** `tests/test_factory.py`：两种 protocol 各得对应类型；非法 protocol 抛 `ProviderError`。

## T12: TUI 组件

**文件：** `mewcode/tui/widgets.py`
**依赖：** T3
**步骤：**
1. `Banner`：渲染 ASCII 猫咪 + `MewCode v{__version__}` + 当前工作目录；下方一行就绪提示。
2. `UserMessage`、`AssistantMessage`（含纯文本流式区，支持增量 append；提供「用 markdown 重渲染整段」的方法）、`ErrorMessage`（可区分样式）。
3. `ThinkingIndicator`：显示 `Imagining… (Ns)`，支持外部每秒更新秒数、结束后可替换为总耗时文案。
4. `PromptInput`：多行输入控件，`❯` 提示符 + 占位符 "Send a message..."；Alt+Enter 插入换行、Enter 触发提交消息（发出 Textual message/事件）；可清空、可禁用（流式期锁定）。
5. 状态栏：左 provider 名、右 model。
**验证：** `python -c "import mewcode.tui.widgets"` 无报错；组件类可构造（渲染行为在 T14 pilot 中一并驱动验证）。

## T13: Provider 选择屏

**文件：** `mewcode/tui/screens.py`
**依赖：** T5, T12
**步骤：**
1. `ProviderSelectScreen(Screen)`：用 `ListView` 列出 `AppConfig.providers` 每项「name — model」；方向键移动、回车选定。
2. 选定后通过回调/`dismiss(selected_cfg)` 把选中的 `ProviderConfig` 返回给 App。
**验证：** `tests/test_screens.py` 用 `App.run_test()` 挂载该屏，模拟按下回车，断言返回的是列表首项对应的 `ProviderConfig`。

## T14: 主应用与对话编排

**文件：** `mewcode/tui/app.py`
**依赖：** T6, T11, T12, T13
**步骤：**
1. `MewCodeApp(App)`：构造接收 `AppConfig`；`compose` 布局 banner + 就绪行 + 对话区(可滚动) + `PromptInput` + 状态栏。
2. `on_mount`：`app_config.single()` 有值→`create_provider` 并进入对话、更新状态栏；否则 `push_screen(ProviderSelectScreen)`，选定回调里 `create_provider`、更新状态栏、聚焦输入。
3. 提交处理（async，收到 `PromptInput` 提交事件）：内容为 `/exit`→退出；否则 `conversation.add_user(text)`+渲染 `UserMessage`、清空并禁用输入 → 新建 `AssistantMessage` + `ThinkingIndicator`（记录起始时间，`set_interval(1s)` 刷新 `Imagining… (Ns)`）→ `async for ev in provider.stream(conversation.build_context())`：`TEXT_DELTA`→逐字 append 纯文本、`ERROR`→渲染 `ErrorMessage`、`DONE`→停止 interval、把整段正文用 markdown 重渲染定型、显示总耗时 → `conversation.add_assistant(正文)` → 启用并聚焦输入；对话区滚到底。
4. 绑定 Ctrl+C 退出（Textual 默认即可，确认退出恢复终端）。
**验证：** `tests/test_app.py` 用 `App.run_test()` 注入假 `Provider`（`stream` 依次 yield 两个 text_delta、done）与单 provider 的 `AppConfig`：模拟输入并提交，断言——对话区出现正文、`conversation.messages` 增加 user+assistant 两条、输入在结束后恢复可用；另注入 yield `error` 的假 Provider，断言渲染 `ErrorMessage` 且应用不退出、输入恢复；输入 `/exit` 断言应用退出。

## T15: CLI 装配与入口

**文件：** `mewcode/cli.py`、`mewcode/__main__.py`
**依赖：** T5, T14
**步骤：**
1. `cli.py` 的 `main() -> int`：`load_config()` → `MewCodeApp(app_config).run()` → 返回 0；顶层 `try/except MewCodeError` 打印可读信息到 stderr、返回非零。**无行为性 flag。**
2. `__main__.py`：`from mewcode.cli import main; raise SystemExit(main())`。
**验证：** `tests/test_cli.py`：monkeypatch `load_config` 抛 `ConfigError` 时 `main()` 返回非零且 stderr 有可读信息；monkeypatch `MewCodeApp.run` 为 no-op 时正常配置下 `main()` 返回 0。`python -m mewcode`（无配置文件）打印可读错误并非零退出。

## 执行顺序

```
T1
├─ T2 ── T5 ──┐
├─ T3 ──┬─ T7 ┼─ T9 ┐
│       │     │      ├─ T11 ┐
│  T8 ──┴─────┴─ T10┘       │
├─ T4 ──┐                   │
│       └─ T6 ──────────────┤
├─ T3 ── T12 ──┬─ T13 ──────┤
│              └─ T14 ───────┤
│                            └─ T15
```

说明：T2/T3/T4 在 T1 后可并行；T5 依赖 T2；T6 依赖 T3+T4；T7 依赖 T3；T8 独立；T9/T10 依赖 T5+T7+T8（可并行）；T11 依赖 T9+T10；T12 依赖 T3；T13 依赖 T5+T12；T14 依赖 T6+T11+T12+T13；T15 依赖 T5+T14。
