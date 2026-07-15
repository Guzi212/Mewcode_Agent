# MewCode Agent Loop Plan

## 架构概览

以“Provider → Agent Loop → TUI”三层编排替换当前由 TUI 直接执行两次模型请求和工具批次的单轮流程。

```text
用户输入
  → AgentLoop（命令解析、循环、停止条件、状态）
  → Provider（协议流）
  → ToolExecutor（保序调度）
  → AgentEvent（统一事件流）
  → TUI（渲染）
```

Provider 只负责将协议流归一化为文本、完整工具调用、用量、错误和结束信号。AgentLoop 是唯一的循环编排入口，独占 Conversation 的写入，维护轮次、模式、累计用量、连续“全未知工具”轮数、取消信号和会话内待执行计划。ToolExecutor 在 AgentLoop 的调用下执行安全分批调度。TUI 仅提交输入、提供既有的路径授权界面并消费 AgentEvent 渲染，不再直接请求 Provider 或执行工具。

## 核心数据结构

### Provider 流事件

保留 `StreamEvent` 作为 Provider → Agent 的归一化事件，扩展一种 Token 用量事件：

```python
class StreamEventKind(str, Enum):
    TEXT_DELTA
    TOOL_CALL
    USAGE
    ERROR
    DONE

@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None
    output_tokens: int | None
```

`USAGE` 表示本轮到当前为止已知的用量快照。OpenAI 在请求中开启流式用量返回；Anthropic 从其开始和结束流事件取得输入、输出用量。兼容端点不提供时字段为 `None`，不估算。

### Agent 事件

新增 Agent → TUI 的独立事件契约：

```python
class AgentEventKind(str, Enum):
    USER_MESSAGE
    TEXT_DELTA
    TOOL_STARTED
    TOOL_FINISHED
    USAGE_UPDATED
    ITERATION_STARTED
    ROUND_FINISHED
    ERROR
    TASK_FINISHED

class StopReason(str, Enum):
    COMPLETED
    ITERATION_LIMIT
    CANCELLED
    UNKNOWN_TOOL_LIMIT
    STREAM_ERROR

@dataclass(frozen=True)
class AgentEvent:
    kind: AgentEventKind
    iteration: int = 0
    text: str = ""
    call: ToolCall | None = None
    result: ToolResult | None = None
    usage: TokenUsage | None = None
    stop_reason: StopReason | None = None
```

### AgentLoop

```python
class AgentLoop:
    async def run(self, text: str) -> AsyncIterator[AgentEvent]: ...
    def cancel_current(self) -> bool: ...
```

AgentLoop 维护单次会话的 Conversation 和待执行计划。每次运行保存当前轮次、取消事件、连续“整轮请求全部未知工具”的次数、当前模式和累计用量。默认最大轮次为 20；配置值无效时回退 20。

### 对话历史

调整 `ConversationItem`，使一个模型回合可同时保存文本与工具调用：

```python
@dataclass(frozen=True)
class ConversationItem:
    kind: ConversationItemKind
    text: str = ""
    calls: tuple[ToolCall, ...] = ()
    results: tuple[ToolResult, ...] = ()
```

有工具调用的模型回合使用一个 assistant 项保存前置文本和调用；紧随其后写入与调用一一对应的工具结果项。OpenAI 将其序列化成一个带 `content` 与 `tool_calls` 的 assistant 消息；Anthropic 将其序列化成同一 assistant 内容块中的文本与 `tool_use`。任何提前终止都补齐结果后再结束，避免下一次请求出现悬空调用。

### 工具安全和执行过程

工具抽象增加只读/副作用安全属性，注册中心据此导出只读工具定义并判定调用类别。执行器以过程事件报告执行：

```python
class ToolExecutor:
    async def execute_batch(
        self,
        calls: list[ToolCall],
        cancel_requested: asyncio.Event,
    ) -> AsyncIterator[ToolExecutionEvent]: ...
```

`ToolExecutionEvent` 只表达开始或结束及对应调用/结果。执行器按原始顺序扫描：连续只读调用组成并发批；写、改、命令等副作用调用各自串行。并发批完成后，结果仍按原调用顺序报告。取消时停止在途工作，并为未得到结果的调用返回 `cancelled` 结构化结果。

## 模块设计

### `mewcode/agent.py`

**职责：** 提供 ReAct 循环、命令解析、停止判断、Plan Mode、Token 汇总、取消协调和 AgentEvent 输出。

**对外接口：** `AgentLoop.run()` 与 `AgentLoop.cancel_current()`。

**依赖：** `Conversation`、`Provider`、`ToolRegistry`、`ToolExecutor`、提示词和配置。

处理规则：

- 普通输入使用完整工具集。
- `/plan <任务>` 使用计划态提示和只读工具集；最终无工具文本保存为待执行计划。
- `/do` 用内置执行提示和完整工具集执行待执行计划；没有计划时发布可恢复错误，不请求模型。
- `/do <任务>` 以完整工具集执行新任务。
- 每轮所有调用都不在注册中心时，未知计数加一；任何一轮含已登记工具即清零。达到三轮时，把本轮未知调用的结构化结果写入历史后结束，不开始下一轮。
- 迭代达到上限、Provider 流错误、取消和自然完成均发布对应 `TASK_FINISHED` 原因。

### `mewcode/messages.py`

**职责：** 定义跨层消息、Provider 流事件、TokenUsage、AgentEvent、停止原因和工具执行过程事件。

**对外接口：** Provider 和 AgentLoop 使用 `StreamEvent`；TUI 只使用 `AgentEvent`。

### `mewcode/conversation.py`

**职责：** 维护可序列化、合法且不持久化的会话历史。

**对外接口：** 新增记录“模型文本加工具调用”的单个回合、记录已配对结果、以指定系统提示构建历史的操作。

**依赖：** `ConversationItem`、`ToolCall`、`ToolResult`。

### `mewcode/prompts.py`

**职责：** 保存默认 Agent 提示、计划态提示和执行计划的内置指令。

**对外接口：** AgentLoop 根据当前模式组合系统提示与内部执行指令。

**约束：** 默认提示删除“工具结果后的最终答复不得再请求工具”的单轮限制；计划态提示明确只分析、不执行副作用。

### `mewcode/providers/base.py`、`openai.py`、`anthropic.py`

**职责：** 维持协议适配和合法历史序列化，并产生用量流事件。

**对外接口：** `Provider.stream(history, tools)` 签名保持不变，事件增加 `USAGE`。

**约束：** OpenAI 使用单个 assistant 消息组合文本与工具调用，并请求可选流式用量；Anthropic 使用一个 assistant content 列表组合文本块和工具块。两者均继续在 Provider 内拼接工具调用分片、丢弃思考增量并将网络/HTTP 错误转为事件。

### `mewcode/tools/base.py`、`registry.py`

**职责：** 给每项工具声明安全类别，并按类别导出定义。

**对外接口：** 注册中心提供完整工具定义、只读工具定义、按名称查找与安全类别查询。

**约束：** `read_file`、`find_files`、`search_code` 为只读；`write_file`、`edit_file`、`run_command` 为副作用。

### `mewcode/tools/executor.py`

**职责：** 在既有沙箱与授权机制之上执行保序分批调度。

**对外接口：** 以 `ToolExecutionEvent` 异步迭代器替代仅返回结果列表的批量执行接口。

**约束：** 每个调用保留原有独立超时；一次失败不阻断同批其余调用；取消后主动清理内部任务，不留下挂起任务或队列。

### `mewcode/config.py` 与 `mewcode.yaml.example`

**职责：** 配置 Agent 最大迭代次数。

**对外接口：** `AppConfig.agent_max_iterations`。

**约束：** 顶层可选 `agent_max_iterations` 为正整数；缺失、非整数或不大于零时使用 20。

### `mewcode/tui/app.py` 与 `widgets.py`

**职责：** 后台消费 AgentEvent 并维护界面状态。

**对外接口：** `Esc` 触发当前 Agent 任务的取消请求；StatusBar 提供用量更新；等待区提供轮次更新。

**约束：** TUI 使用 Textual 后台 worker 读取事件流，避免阻塞消息处理；保留 `Ctrl+C` 的退出绑定和既有外部路径审批 modal。工具行在 `TOOL_STARTED` 时创建，在 `TOOL_FINISHED` 时完成；最终事件恢复输入。

## 模块交互

```text
TUI 输入
  → AgentLoop 解析命令并发布 USER_MESSAGE
  → Conversation 写入用户或内置执行指令
  → AgentLoop 发布 ITERATION_STARTED
  → Provider.stream（当前模式的工具定义）
  → AgentLoop 发布文本、用量、错误事件并收集整轮
  → Conversation 写入模型回合
  → ToolExecutor 发布开始/结束事件
  → Conversation 写入按原序配对的结果
  → 下一轮，或发布 ROUND_FINISHED / TASK_FINISHED
  → TUI 只依据 AgentEvent 渲染
```

取消时，AgentLoop 不取消自身的事件生成器；它取消当前 Provider/执行器内部工作并等待清理，从而有机会补齐结果、写入合法历史并发布结束事件。TUI 后台 worker 因正常结束而退出。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 编排归属 | 新增独立 AgentLoop | 将循环与 UI 解耦，便于单测和协议一致性。 |
| Provider 兼容性 | 保留 StreamEvent，新增 AgentEvent | 不让协议细节泄漏到 UI，也避免不必要的大范围 Provider 改写。 |
| 历史表示 | 文本与工具调用合并为模型回合 | 满足两种 API 对 assistant 工具回合的序列约束。 |
| 并发策略 | 仅连续只读并发 | 保留模型顺序且不猜测依赖。 |
| Plan Mode 防护 | 定义过滤加 Agent 兜底拒绝 | 即使模型违背提示也不会执行副作用。 |
| Token 统计 | Provider 发送本轮快照、Agent 轮末累计 | 避免 Anthropic 流式输出用量重复累计。 |
| 取消 | 取消内部子任务而非 AgentLoop 本身 | 能保证清理与历史配对。 |
| `/do` 无计划 | 可恢复错误，不请求模型 | 避免在没有明确任务时产生不受控执行。 |

## 文件组织

```text
mewcode/
├── agent.py                    # 新增：AgentLoop、模式、停止状态、循环
├── messages.py                 # Provider/Agent/执行过程事件与 TokenUsage
├── conversation.py             # 合法的模型回合与工具结果历史
├── prompts.py                  # 默认、计划、执行计划提示
├── config.py                   # agent_max_iterations
├── providers/
│   ├── base.py                 # 更新统一流事件契约
│   ├── openai.py               # 用量与合法工具回合序列化
│   └── anthropic.py            # 用量与合法工具回合序列化
├── tools/
│   ├── base.py                 # 工具安全类别
│   ├── models.py               # ToolExecutionEvent
│   ├── registry.py             # 只读筛选与类别查询
│   └── executor.py             # 保序分批并发与取消
└── tui/
    ├── app.py                  # 消费 AgentEvent、Esc 取消
    └── widgets.py              # 轮次和 Token 展示

mewcode.yaml.example            # agent_max_iterations 示例
tests/
├── test_agent.py               # 新增：循环、停止、Plan Mode、取消、事件
├── test_conversation.py        # 合法跨轮历史
├── test_tool_registry.py       # 安全类别和只读筛选
├── test_tool_executor.py       # 分批并发、顺序、取消
├── test_openai.py              # 流式用量和历史序列化
├── test_anthropic.py           # 流式用量和历史序列化
├── test_config.py              # 迭代上限默认与回退
└── test_app.py                 # 事件渲染、Esc、Token、轮次
```
