# OpenAI 兼容思考模式 Plan

## 架构概览

沿用现有 `OpenAIProvider → StreamEvent → AgentLoop → Conversation → OpenAIProvider` 闭环，不增加 Provider 实例级缓存或旁路回调。

OpenAI Provider 不识别模型厂商。配置层将 `thinking` 保存为可选布尔值，以区分 true、false 和未配置。OpenAI Provider 只在配置明确出现该字段时发送对应扩展参数；任何 OpenAI 兼容响应只要实际返回 `reasoning_content`，就转换为仅供 Agent Loop 消费的内部流事件。该事件使用独立字段承载内容，不复用公开正文的 `text` 字段。

Agent Loop 在每轮内部累积思考增量，但不转换为 `AgentEvent`，因此 TUI 不会收到思考内容。若本轮产生工具调用，Agent Loop 将完整思考内容与同一助手回合一起写入 `Conversation`；若本轮没有工具调用，则在最终正文完成后丢弃该轮思考内容。

Conversation 的协议无关历史项增加可选隐藏思考字段。OpenAI 序列化器仅在历史项实际包含该字段时把它写为 `reasoning_content`，并保证这类工具调用助手消息的 `content` 至少为空字符串而不是 `null`。Anthropic 序列化器不读取该字段；未收到思考内容的 OpenAI 响应继续使用既有序列化结果。

```text
OpenAI-compatible SSE
  ├─ reasoning_content ─► 内部 StreamEvent ─► AgentLoop 临时累积
  ├─ content ───────────► 正文 StreamEvent ─► AgentEvent ─► TUI
  └─ tool_calls ────────► 工具调用事件 ─────► AgentLoop
                                      │
                         若存在工具调用，原子写入同一助手历史项
                                      │
                    content + reasoning_content + tool_calls
                                      │
                              ToolResult 紧随其后
                                      │
                         下一次 DeepSeek 请求完整序列化
```

## 核心数据结构

### 内部思考流事件

```python
class StreamEventKind(str, Enum):
    # 既有成员保持不变
    REASONING_DELTA = "reasoning_delta"

@dataclass(frozen=True)
class StreamEvent:
    kind: StreamEventKind
    text: str = ""
    call: ToolCall | None = None
    usage: TokenUsage | None = None
    reasoning: str = ""

    @classmethod
    def reasoning_delta(cls, value: str) -> "StreamEvent": ...
```

`reasoning` 与 `text` 分离，防止既有正文分支误把思考内容转发给界面。`REASONING_DELTA` 只在 Provider 与 Agent Loop 之间流动；`AgentEventKind` 不增加对应成员。

### 会话历史项

```python
@dataclass(frozen=True)
class ConversationItem:
    kind: ConversationItemKind
    text: str = ""
    calls: tuple[ToolCall, ...] = ()
    results: tuple[ToolResult, ...] = ()
    reasoning_content: str = ""

    @classmethod
    def assistant(
        cls,
        text: str,
        calls: list[ToolCall] | None = None,
        reasoning_content: str = "",
    ) -> "ConversationItem": ...
```

`reasoning_content` 只对实际返回该字段且带工具调用的 OpenAI-compatible 助手回合赋值。字段默认空字符串，保持现有构造调用兼容。

### Provider 配置

```python
@dataclass
class ProviderConfig:
    # 其他字段保持不变
    thinking: bool | None = None
```

`None` 表示 YAML 未配置：Anthropic 继续按关闭处理，OpenAI Provider 不发送任何 `thinking` 扩展。`True` 与 `False` 表示用户明确要求 OpenAI-compatible 扩展开启或关闭。配置加载器只接受真实布尔值。

### Conversation 写入入口

```python
def add_assistant_turn(
    self,
    text: str,
    calls: list[ToolCall],
    reasoning_content: str = "",
) -> None: ...
```

调用方负责只在 `calls` 非空时传入思考内容。Conversation 原子创建一个同时包含前置正文、思考内容和工具调用的助手历史项，避免拆成多个无法正确序列化的消息。

## 模块设计

### `mewcode/providers/openai.py`

**职责：** OpenAI-compatible 扩展开关、SSE 思考增量解析和响应驱动历史序列化。

不增加模型 ID、厂商或地址判断。请求行为只取决于配置是否显式出现 `thinking`：

请求构造规则：

```python
if config.thinking is not None:
    body["thinking"] = {
        "type": "enabled" if config.thinking else "disabled"
    }
```

解析规则：任何 OpenAI 兼容响应只要返回非空字符串 `reasoning_content`，就发布为内部思考事件。服务端响应本身是能力信号，不需要预先知道模型厂商。

序列化时，为非空 `ConversationItem.reasoning_content` 加入同名 API 字段，并把该类带工具调用但没有前置正文的助手 `content` 写为 `""`。没有思考历史的普通 OpenAI 工具消息保持现有 `None` 行为。这样兼容性由实际响应驱动，而不是模型名单驱动。

### `mewcode/config.py`

**职责：** 保留 `thinking` 是否显式配置的信息。

配置缺失映射为 `None`，YAML `true`/`false` 分别映射为布尔值；字符串、数字、列表等值在启动期产生不包含敏感信息的 `ConfigError`。Anthropic 现有真值判断可自然把 `None` 视为关闭。

### `mewcode/agent.py`

**职责：** 每轮临时累积内部思考内容，并决定是否写入历史。

每次迭代创建独立 `reasoning_parts`。收到内部思考事件时只追加到该列表，不产生任何 `AgentEvent`。流结束后：

- 有工具调用：把完整思考内容随正文和调用写入助手历史；
- 无工具调用：只写最终正文，思考内容随局部变量释放；
- 完整工具调用已入历史后取消：沿用现有取消结果补全逻辑；
- 工具调用尚未完成就取消或流错误：不创建孤立思考历史。

### `mewcode/messages.py` 与 `mewcode/conversation.py`

**职责：** 提供协议无关但可携带隐藏 Provider 元数据的数据契约。

`messages.py` 增加内部事件类型、独立思考字段和历史字段；文档明确“不得公开发布”而不是“在 Provider 内直接丢弃”。`conversation.py` 扩展助手回合写入入口，旧调用方式保持可用。

### 测试

`tests/test_openai.py` 覆盖请求体、DeepSeek 模型隔离、SSE 内部事件和 `reasoning_content` 序列化。`tests/test_agent.py` 覆盖思考内容不进入公开事件、工具回合保存、连续工具回灌、取消与错误恢复。`tests/test_conversation.py` 覆盖助手正文、隐藏思考和工具调用保持同一历史项。

真实 API 验收使用用户已有的忽略 Git 配置，不读取或输出密钥。若当前 Windows 会话没有 tmux，优先检查 WSL 内是否已有 tmux 与可用项目运行环境；缺失时明确报告该项环境阻塞，不静默改用非 tmux 结果冒充通过。

## 模块交互

1. Provider 根据 `thinking` 是否显式配置决定发送扩展开关，不检查模型或地址。
2. OpenAI 兼容端点返回 SSE；Provider 根据实际字段分别发布内部思考、正文、工具调用和用量事件。
3. Agent Loop 只把正文等公开事件转成 `AgentEvent`，思考增量留在当前迭代局部状态。
4. 出现工具调用时，Agent Loop 将正文、完整思考和工具调用写为同一助手历史项。
5. 工具执行结果按既有顺序追加。
6. 下一次请求序列化完整助手消息及工具结果，兼容端点继续推理。
7. 最终无工具调用的文本回合正常结束，最终思考内容不保存也不展示。

## 文件组织

```text
mewcode/
├── messages.py                  # 内部思考事件与隐藏历史字段
├── config.py                    # thinking 三态配置与类型校验
├── conversation.py              # 原子保存助手思考工具回合
├── agent.py                     # 累积、隔离并按条件保存思考内容
└── providers/
    ├── base.py                  # 更新统一 Provider 契约说明
    └── openai.py                # DeepSeek 开关、解析与回传
mewcode.yaml.example             # 说明 DeepSeek thinking 配置语义
tests/
├── test_openai.py               # 请求与协议测试
├── test_agent.py                # Agent Loop 隔离和回灌测试
└── test_conversation.py         # 历史关联测试
specs/05-deepseek-thinking/      # 本规格四份文档
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 思考内容跨层方式 | 独立内部 `StreamEvent` | 沿用异步流，避免 Provider 可变缓存和隐藏回调 |
| 内容承载字段 | `reasoning` 与正文 `text` 分离 | 降低被既有正文分支意外展示的风险 |
| 历史归属 | 与同轮助手工具调用原子保存 | 符合 DeepSeek 消息结构和现有工具历史顺序 |
| 最终回合处理 | 无工具调用时不保存思考内容 | 协议无需回传，减少内存和泄漏面 |
| 能力启用 | 显式三态配置 | 不猜模型能力；未配置时保持严格 OpenAI 兼容 |
| 思考回传 | 根据实际响应字段 | 任意采用相同扩展的兼容端点可直接复用 |
| 空工具前置正文 | 仅带思考历史的工具消息使用空字符串 | 避免扩展工具回合产生 `content: null`，不改变普通消息 |
| false 语义 | 显式发送 disabled | 不依赖 DeepSeek 默认 enabled，配置行为确定 |
| 推理强度 | 本期不增加 | 用户只要求完整开关与工具兼容，避免扩展范围 |
| Anthropic | 保持原实现 | 不把 DeepSeek 修复扩展成另一套协议重构 |

## Spec 覆盖

| Spec | 设计归属 |
|---|---|
| F1 | 配置三态校验与 OpenAI Provider 请求构造 |
| F2、F5 | 内部思考事件、Agent Loop 隔离 |
| F3、F4 | ConversationItem、Conversation、OpenAI 序列化 |
| F6 | Agent Loop 的取消与错误分支 |
| F7 | 无厂商判断的配置/响应驱动规则与协议回归测试 |
| F8 | 既有 Provider/Agent 错误事件路径与新增回归测试 |
