# MewCode 工具系统 Plan

## 架构概览

工具系统由五个层次组成：工具定义与注册、系统级沙箱与授权、协议适配、会话编排和 TUI 呈现。

`ToolRegistry` 保存六项固定工具的声明，向 Provider 提供协议无关的工具定义，并按模型返回的名称定位执行对象。`ToolExecutor` 是唯一执行入口：它验证参数、计算所需的路径和权限、向授权界面请求外部访问许可，并经 `Sandbox` 启动受限的工具工作进程。工作进程承担实际的文件、搜索和命令操作；主应用不将 API 密钥传给该进程。

会话层将纯文本消息扩展为可表达助手工具调用和工具结果的协议无关历史项。Anthropic 与 OpenAI Provider 分别把历史项和工具定义序列化为各自 API 格式，并将流式工具调用碎片归一化为完整 `ToolCall` 事件。TUI 在首次流式响应结束后顺序执行收集到的工具批次、显示工具行和摘要、将调用与结果写回历史，然后仅再请求一次模型以生成最终文本。

```text
TUI App
  │ 首次请求（历史 + 工具定义）
  ▼
Provider（Anthropic / OpenAI）
  │ TEXT_DELTA / TOOL_CALL
  ▼
Conversation ──► ToolRegistry ──► ToolExecutor ──► Permission UI
                                             │
                                             ▼
                                  Sandbox Worker Process
                                             │
                                             ▼
                                      ToolResult batch
                                             │
  ◄──────────── 追加调用与结果 ───────────────┘
  │ 一次续答请求（不再执行工具）
  ▼
最终文本答复
```

## 核心数据结构

### `ToolDefinition`

```python
@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, object]
```

工具的协议无关元信息。`input_schema` 使用 JSON Schema 的对象子集；注册中心直接据此导出两种协议所需的参数定义。

### `ToolCall`

```python
@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, object]
```

模型请求一次工具调用的完整表示。`id` 用于把结果准确关联回对应协议的历史消息。

### `ToolResult` 与 `ToolError`

```python
@dataclass(frozen=True)
class ToolError:
    code: str
    message: str

@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    ok: bool
    output: str
    summary: str
    truncated: bool = False
    error: ToolError | None = None
```

所有工具都返回 `ToolResult`，不以未处理异常向 TUI 或 Provider 冒泡。`output` 是回灌模型的受限文本，`summary` 是 TUI 工具行展示内容；失败时 `error.code` 区分参数、权限、路径、超时、命令退出和内部错误。

### `ConversationItem`

```python
class ConversationItemKind(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_CALLS = "tool_calls"
    TOOL_RESULTS = "tool_results"

@dataclass(frozen=True)
class ConversationItem:
    kind: ConversationItemKind
    text: str = ""
    calls: tuple[ToolCall, ...] = ()
    results: tuple[ToolResult, ...] = ()
```

会话历史使用此协议无关表示。Provider 负责将 `TOOL_CALLS` 和 `TOOL_RESULTS` 转换为 OpenAI 的 assistant/tool 消息或 Anthropic 的 tool_use/tool_result 内容块。

### `StreamEvent`

```python
class StreamEventKind(str, Enum):
    TEXT_DELTA = "text_delta"
    TOOL_CALL = "tool_call"
    ERROR = "error"
    DONE = "done"

@dataclass(frozen=True)
class StreamEvent:
    kind: StreamEventKind
    text: str = ""
    call: ToolCall | None = None
```

Provider 只在工具名称和 JSON 参数已完整、有效解析后产出 `TOOL_CALL`。中间碎片仅在 Provider 内部累积；推理内容延续现有行为，识别后丢弃。

### `AccessRequest`、`AccessGrant` 与 `SandboxRequest`

```python
class AccessMode(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"

class ApprovalScope(str, Enum):
    DENY = "deny"
    ONCE = "once"
    SESSION = "session"

@dataclass(frozen=True)
class AccessRequest:
    tool_name: str
    target: Path
    mode: AccessMode
    command_summary: str | None = None

@dataclass(frozen=True)
class AccessGrant:
    target: Path
    mode: AccessMode
    scope: ApprovalScope
```

`SandboxRequest` 由工具执行器生成，包含工作目录、会话内有效授权、工具名称和 JSON 参数。沙箱仅接收已清洗的参数和最小环境变量，不接收 Provider 配置或 API 密钥。

### 核心接口

```python
class Tool(ABC):
    @property
    @abstractmethod
    def definition(self) -> ToolDefinition: ...

    @abstractmethod
    def access_request(self, call: ToolCall) -> AccessRequest | None: ...

    @abstractmethod
    async def execute(self, call: ToolCall, executor: ToolExecutor) -> ToolResult: ...

class ToolRegistry:
    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool | None: ...
    def definitions(self) -> list[ToolDefinition]: ...

class Sandbox(ABC):
    async def run(self, request: SandboxRequest, timeout: float) -> ToolResult: ...

class Provider(ABC):
    def stream(
        self,
        history: list[ConversationItem],
        tools: list[ToolDefinition],
    ) -> AsyncIterator[StreamEvent]: ...
```

## 模块设计

### `mewcode.tools`

**职责：** 定义工具元信息、输入验证、注册、统一结果契约和六项工具行为。

**对外接口：** `Tool`、`ToolRegistry`、`ToolExecutor`、`ToolCall`、`ToolResult`、`build_default_registry()`。

**依赖：** `mewcode.sandbox`；不依赖 Provider 或 TUI。

六项固定工具名为 `read_file`、`write_file`、`edit_file`、`run_command`、`find_files`、`search_code`。读取输出添加行号；编辑使用精确原文计数，非唯一时返回匹配次数；写入以临时文件和原子替换完成；查找与搜索由工作进程以 Python 标准库执行，避免依赖宿主机的 `rg` 等外部二进制。各类输出统一经过体量限制器，保留“已截断”标记。

`ToolExecutor` 以顺序方式执行一个 `ToolCall` 列表：未知名称、参数不合法、授权拒绝、沙箱不可用、超时和内部异常均转换为对应的 `ToolResult`，一个调用失败不影响后续调用。

### `mewcode.sandbox`

**职责：** 为每次工具调用建立最小权限的系统级执行环境，管理会话内授权，并调用隔离工作进程。

**对外接口：** `Sandbox`、`SandboxFactory`、`PermissionStore`、`AccessRequest`、`AccessGrant`、`SandboxUnavailable`。

**依赖：** 标准库的 `subprocess`、`json`、`pathlib`；平台沙箱后端。

主应用作为权限代理：它只解析真实目标路径、保存当前会话授权并展示审批，不直接读取、写入或执行模型请求的目标。实际工具工作进程接受一行 JSON 输入，输出一行 JSON 结果；沙箱后端将其限制为运行时只读路径、当前工作目录读写、系统临时目录读写和本次批准的额外路径。工作进程不继承 API 密钥、代理凭据或其他敏感环境变量，且网络关闭。

后端工厂按平台选择原生实现：macOS 使用 Seatbelt，Linux/WSL 使用 Bubblewrap，Windows 使用原生 Windows Sandbox。后端缺失、无法启动或无法施加请求的权限时，返回 `sandbox_unavailable` 或 `sandbox_denied`，不降级为未隔离的本地执行。

默认授予当前工作目录读写。请求工作目录外路径时，执行器先检查会话授权；未命中则请求 TUI 审批。单次授权只进入当前 `SandboxRequest`，会话授权按真实路径、读写模式保存至应用退出。读授权不包含写权限，文件授权不隐式授权父目录，解析后的符号链接目标必须落在有效授权范围内。命令工具以默认工作目录执行，并可通过其明确的工作目录参数请求额外目录授权；命令产生的子进程继承同一沙箱边界。

### `mewcode.tool_worker`

**职责：** 在沙箱内部反序列化已验证的请求，运行具体文件、搜索或命令操作，并序列化 `ToolResult`。

**对外接口：** 仅供 `Sandbox` 以子进程调用的 JSON Lines 协议。

**依赖：** `mewcode.tools` 的纯操作函数与标准库；不导入配置、Provider、TUI 或 HTTP 客户端。

工作进程捕获所有异常并产生结构化结果。命令工具独立启动受限 shell 子进程，超时后终止进程组并采集上限内的标准输出和标准错误。

### `mewcode.providers`

**职责：** 把统一历史和工具定义转换为协议请求，解析两种 SSE 格式并归一化输出。

**对外接口：** 扩展后的 `Provider.stream(history, tools)`。

**依赖：** `mewcode.messages` 中的历史项与流事件；不依赖工具执行器、沙箱或 TUI。

OpenAI Provider 在请求中发送 function 工具数组；按工具调用索引累积 `id`、函数名和 `arguments` 碎片，流结束前产出完整调用。Anthropic Provider 在请求中发送工具数组；依据 `content_block_start`、`input_json_delta` 和 `content_block_stop` 累积 tool use 内容块。两者都保留首次文本增量、丢弃 thinking 增量、将 HTTP/网络异常转为错误事件的既有语义。

序列化时，OpenAI 使用 assistant 的 `tool_calls` 与 role 为 tool 的结果消息；Anthropic 使用 assistant 的 `tool_use` 内容块与后续 user 的 `tool_result` 内容块。首次与第二次请求均携带工具定义；第二次产生的 `TOOL_CALL` 会被上层识别为单轮上限，不会执行或触发第三次请求。

### `mewcode.conversation` 与 `mewcode.messages`

**职责：** 维护包含系统提示词、文本消息、工具调用和工具结果的单次会话历史，并定义跨模块数据契约。

**对外接口：** `add_user()`、`add_assistant_text()`、`add_tool_calls()`、`add_tool_results()`、`build_history()`。

**依赖：** 仅标准库与工具数据模型；不依赖具体协议。

原有纯文本消息创建方法保留等价行为，确保没有工具调用的请求序列化结果不变。工具调用与结果以完整批次写入，保证第二次请求有完整、顺序正确的上下文。

### `mewcode.tui`

**职责：** 编排一次用户请求的两段流式调用、顺序工具执行、外部路径授权和工具结果展示。

**对外接口：** `MewCodeApp` 注入 `ToolRegistry`、`ToolExecutor` 和 `Sandbox`；`ToolApprovalScreen` 返回 `ApprovalScope`。

**依赖：** conversation、providers、tools 和 sandbox。

首次流式响应期间，应用实时显示文本，并在收到完整 `TOOL_CALL` 时挂载工具行。首次响应结束后，将调用批次加入历史，逐个显示执行中状态、执行工具、更新摘要并收集结果；再将结果加入历史，流式显示第二次最终答复。第二次响应若包含工具调用，应用将其显示为达到单轮上限的结构化错误，不执行也不再请求模型。

`ToolApprovalScreen` 是 modal screen，显示工具名、真实路径、读/写/执行权限和命令摘要；焦点默认落在拒绝项，用户以方向键选择“拒绝”“仅本次允许”“本会话允许”并以 Enter 确认。授权屏幕、工具执行和第二次请求期间均保持输入框禁用，最终恢复输入焦点。

## 模块交互

1. 应用接收用户输入，向 `Conversation` 写入用户文本，并取得 `registry.definitions()`。
2. 应用调用 `provider.stream(history, definitions)`；Provider 发送协议工具定义，逐步产出文本、完整工具调用、错误或完成事件。
3. 应用实时渲染文本和工具行；完成后把首次响应的文本与工具调用批次写入 `Conversation`。
4. `ToolExecutor` 依次处理工具调用。它从注册中心定位工具、验证参数、检查权限，并在需要时通过 `ToolApprovalScreen` 获得范围化授权。
5. `Sandbox` 用该调用的最小授权启动 `tool_worker`；工作进程返回受上限约束的结构化结果。应用更新工具行，所有结果写入 `Conversation`。
6. 应用第二次调用同一 Provider，流式显示最终文本。若出现工具调用，只显示单轮上限错误；不调用执行器，不开始第三次请求。
7. 任意 Provider、沙箱或工具错误均作为可恢复的结构化结果或错误消息显示；应用清理计时与忙碌状态并恢复输入。

## 文件组织

```text
mewcode/
├── messages.py                 # 扩展会话项、工具调用、工具结果与流事件
├── conversation.py             # 维护工具调用/结果在内的历史
├── prompts.py                  # Agent 角色、工具与沙箱约定
├── tool_worker.py              # 沙箱内 JSON Lines 工具工作进程
├── tools/
│   ├── __init__.py             # 导出默认注册中心与公共类型
│   ├── base.py                 # Tool 抽象和参数校验基类
│   ├── models.py               # ToolDefinition、ToolCall、ToolResult、错误类型
│   ├── registry.py             # ToolRegistry 和六项默认工具登记
│   ├── executor.py             # 顺序执行、超时与结果规范化
│   ├── files.py                # 读、写、唯一替换的工作进程操作
│   ├── search.py               # glob 与文本搜索的工作进程操作
│   └── command.py              # 命令执行、进程组终止和输出限制
├── sandbox/
│   ├── __init__.py             # SandboxFactory 与公共类型导出
│   ├── models.py               # 权限请求、授权、沙箱请求
│   ├── base.py                 # Sandbox 抽象
│   ├── permissions.py          # 真实路径检查和会话授权存储
│   ├── macos.py                # Seatbelt 后端
│   ├── linux.py                # Bubblewrap 后端
│   └── windows.py              # Windows Sandbox 后端
├── providers/
│   ├── base.py                 # 扩展 Provider.stream 契约
│   ├── openai.py               # OpenAI 工具序列化与流式碎片累积
│   └── anthropic.py            # Anthropic 工具序列化与流式碎片累积
└── tui/
    ├── app.py                  # 两段请求编排与工具批次执行
    ├── screens.py              # 外部路径授权 modal
    └── widgets.py              # 工具行和结果摘要组件

tests/
├── test_tool_registry.py       # 注册、查找和工具定义
├── test_tools_files.py         # 读、写、唯一替换与输出上限
├── test_tools_search.py        # glob、文本搜索与无结果
├── test_tools_command.py       # stdout/stderr、非零退出和超时
├── test_sandbox_permissions.py # 路径、符号链接、单次/会话授权与拒绝
├── test_sandbox_backends.py    # 后端选择、不可用时拒绝与环境清洗
├── test_tool_worker.py         # JSON Lines 契约和错误规范化
├── test_openai.py              # 增加工具请求/结果和流碎片测试
├── test_anthropic.py           # 增加工具请求/结果和流碎片测试
├── test_conversation.py        # 工具调用与结果历史顺序
└── test_app.py                 # 单轮闭环、授权 UI、工具行与单轮上限
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 工具执行边界 | 隔离 `tool_worker` 子进程，而非主进程直接操作文件 | Provider 需要网络和 API 密钥；工具子进程可获得独立、最小化的文件、网络与环境边界。 |
| 权限模型 | 默认工作区权限 + 外部路径按读写粒度临时审批 | 既能处理目录外文件，又避免一次性暴露用户目录。 |
| 授权生命周期 | 单次或会话内存授权，不持久化 | 满足外部处理能力，避免配置白名单长期扩大权限。 |
| 沙箱后端 | 平台原生后端；不可用即失败关闭 | 满足系统级强制边界，避免“限制失效后静默放行”。 |
| 工具结果 | 统一 `ToolResult`，文本输出与 TUI 摘要分离 | Provider 回灌、TUI 显示和错误处理使用相同安全契约，长输出可控。 |
| 协议历史 | 协议无关会话项，由 Provider 序列化 | 同时正确表达 OpenAI 和 Anthropic 不同的工具历史格式。 |
| 流式调用 | Provider 内部拼接，完成后才上报 `ToolCall` | 上层无需处理半截 JSON，避免未完整参数被执行。 |
| 单轮限制 | 首次工具批次全部执行，第二次请求只生成答复 | 支持实际“查后总结”闭环，同时严格防止 Agent Loop。 |
| 搜索实现 | 工作进程内使用 Python 标准库 | 不依赖宿主额外命令，减少沙箱下的可执行文件授权。 |
| 网络策略 | 工具工作进程关闭网络；Provider 保持既有联网 | 命令不能借工具通道外传数据，LLM API 调用不受影响。 |
