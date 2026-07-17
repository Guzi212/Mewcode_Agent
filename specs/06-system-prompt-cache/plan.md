# 系统提示工程化 Plan

## 架构概览

本章沿用现有 `Provider → AgentLoop → Conversation → Provider` 调用链，不改变 Provider 的公开 `stream(history, tools)` 签名。改动集中在五个层次：

1. **稳定提示层**：把固定全局指令声明为 `PromptModule`，由唯一装配器确定性排序并生成稳定 System Prompt。
2. **动态上下文层**：异步收集工作目录、平台、日期、Git、应用和模型信息，连同 Plan Mode 提醒构造为带 `<system-reminder>` 标签的系统级补充项。
3. **会话请求层**：`Conversation` 只在构造当前请求时插入补充项；补充项使用独立类型，不进入持久历史，也不因用户输入同名标签而提升权限。
4. **协议与缓存层**：Anthropic 将稳定工具和稳定系统块显式标记为可缓存；OpenAI-compatible 保持稳定前缀顺序并使用端点自动缓存。两个 Provider 把补充项序列化为协议允许的系统级内容。
5. **遥测与验收层**：统一用量增加可选缓存读写指标，Agent Loop 以“每轮最终快照”累计；调试脚本和人工 A/B 文档观察实际缓存结果，不改变 TUI 状态栏。

```text
固定 PromptModule ──► 稳定系统提示 ─────────────────────────────┐
稳定工具注册表 ─────► 稳定工具定义 ─────────────────────────────┤
                                                               ▼
环境采集 ───────────► SYSTEM_REMINDER ─► Conversation.build_history
Plan 轮次策略 ──────► SYSTEM_REMINDER ────────────────┘        │
持久 ConversationItems ────────────────────────────────────────┘
                                                               │
                    ┌──────────────────────────────────────────┴─────────────┐
                    ▼                                                        ▼
       Anthropic：tools/system cache_control                    OpenAI：稳定前缀自动缓存
                    │                                                        │
                    └──────────── StreamEvent.usage ─────────────────────────┘
                                           │
                                           ▼
                           AgentLoop 每轮最终快照与会话累计
```

## 核心数据结构

### `PromptModule`

```python
@dataclass(frozen=True)
class PromptModule:
    name: str
    priority: int
    content: str
```

- `name` 用于诊断与测试，不写入提示正文。
- `priority` 数值越大越靠前；相同优先级依赖 Python 稳定排序保留声明顺序。
- `content` 去除首尾空白后为空时跳过。

固定七个模块和三个可选空槽集中声明为不可变元组，作为模块顺序与正文的单一权威来源。

### 提示装配与提醒接口

```python
def build_system_prompt(
    modules: Iterable[PromptModule] = DEFAULT_PROMPT_MODULES,
) -> str: ...

def build_system_reminder(content: str) -> str: ...

def build_plan_mode_reminder(iteration: int) -> str: ...
```

- `build_system_prompt` 过滤空内容、按优先级稳定排序，用一个空行连接。
- `build_system_reminder` 统一生成 `<system-reminder>...</system-reminder>`，不接受角色参数。
- `build_plan_mode_reminder` 在 `(iteration - 1) % 5 == 0` 时返回完整提醒，其余轮次返回只读约束精简提醒；`iteration` 必须从 1 开始。

为保持现有导入兼容，`SYSTEM_PROMPT` 由默认模块在导入期确定性构建；旧 `PLAN_MODE_PROMPT` 仅保留为完整提醒正文的兼容常量，Agent Loop 不再把它拼入稳定提示。

### 动态环境接口

```python
async def build_environment_reminder(
    provider_name: str,
    model: str,
    *,
    cwd: Path | None = None,
    today: date | None = None,
) -> str: ...
```

返回一个完整的 `<system-reminder>`。工作目录默认由 `Path.cwd()` 获取，日期默认使用当前本地日期；可注入参数用于确定性测试。Git 摘要在工作线程中用参数数组调用 `git`，设置非交互环境并使用短超时，不通过 Shell 执行。

Git 摘要只输出：

- 是否为 Git 仓库；
- 当前分支或 detached 状态；
- 工作区 clean/dirty 与改动条目数。

不输出文件名、文件内容、remote URL、提交信息或环境变量值。任一子步骤失败时返回安全的“不可用”或“非 Git 仓库”状态。

### 系统级补充项

```python
class ConversationItemKind(str, Enum):
    # 既有类型保持不变
    SYSTEM_REMINDER = "system_reminder"

@dataclass(frozen=True)
class ConversationItem:
    # 既有字段保持不变

    @classmethod
    def system_reminder(cls, text: str) -> "ConversationItem": ...
```

该类型只能由可信的运行时代码创建。用户消息即使包含相同 XML 标签，`kind` 仍是 `USER`，Provider 根据内部类型而不是字符串判断角色。

### 请求历史构造

```python
def build_history(
    self,
    system_prompt: str | None = None,
    system_reminders: Sequence[str] = (),
) -> list[ConversationItem]: ...
```

输出顺序固定为：稳定 `SYSTEM` → 当前请求的零个或多个 `SYSTEM_REMINDER` → 持久历史。`system_reminders` 只创建返回列表中的临时项，不追加到 `Conversation.items` 或公开 `messages`。

### 统一缓存用量

```python
@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
```

- `input_tokens` 始终表示 Provider 语义下的总输入 Token。
- 缓存明细缺失或非法时为 `None`，与真实的 `0` 区分。
- Provider 每次只发布当前请求的最新累计快照；Agent Loop 保存本轮最后一个快照，并在本轮结束时仅合并一次。
- 多轮会话累计缓存字段时，只要所有已累计轮次都未知则保持 `None`；已知值按轮相加，未知轮次不伪造为零。

## 模块设计

### `mewcode/prompts.py`

**职责：** 系统模块声明、稳定装配、系统提醒标签和 Plan Mode 轮次策略。

七个固定模块使用中文正文，内容分别覆盖身份、系统约束、模式语义、动作执行、工具使用、语气和文本输出。工具使用模块包含四条双重强化规则；系统约束和任务模式模块说明 reminder 的优先级、不可直接回复语义以及提示词不是权限边界。

装配器不识别模块名称，不为可选模块编写特殊分支。三个空槽与固定模块使用完全相同的数据结构和排序规则。

### `mewcode/environment.py`

**职责：** 构造每轮动态环境 reminder。

同步平台字段直接读取；Git 子进程通过 `asyncio.to_thread` 移出事件循环。命令使用 `git status --porcelain=v1 --branch --untracked-files=normal` 的首行与条目数量生成摘要，超时上限为 1 秒，并设置 `GIT_TERMINAL_PROMPT=0`、`GIT_OPTIONAL_LOCKS=0`。所有异常在字段边界内降级，不把原始异常、命令输出或配置泄漏给模型。

### `mewcode/messages.py` 与 `mewcode/conversation.py`

**职责：** 扩展协议无关消息和用量契约，并保证补充项不持久化。

`messages.py` 增加 `SYSTEM_REMINDER` 与缓存用量字段，既有构造调用依靠默认值保持兼容。`conversation.py` 在请求构造阶段创建临时补充项；`add_user`、`add_assistant_turn`、工具结果写入和历史恢复路径均不接受 reminder，以缩小可信创建面。

### `mewcode/agent.py`

**职责：** 每轮装配动态上下文、控制 Plan 提醒节奏并正确累计用量。

每次 `run()` 从第 1 轮计数。每轮请求前重新构造环境 reminder；处于 Plan Mode 时再加入该轮完整或精简提醒。随后使用 Conversation 自带的稳定 System Prompt 和临时 reminders 构造历史。工具集仍由 `ToolRegistry.definitions(read_only=plan_mode)` 过滤，执行层的副作用拒绝逻辑保持不变。

Agent Loop 对每个 Provider 请求只保留最后一次 `TokenUsage` 快照。请求结束、流错误、取消或达到迭代上限时，沿用既有提交边界，把最终快照合并进会话累计一次。TUI 继续只读取输入和输出总量。

### `mewcode/providers/anthropic.py`

**职责：** 把统一消息映射到 Anthropic 内容块，标记显式缓存边界并解析缓存用量。

请求结构：

- 稳定 `SYSTEM` 序列化为 system text block，并附加 `cache_control: {"type": "ephemeral"}`。
- `SYSTEM_REMINDER` 序列化为后续 system text block，不带 `cache_control`。
- 工具定义按注册表顺序输出，只在最后一个稳定工具定义上添加同样的缓存控制，以覆盖之前的稳定工具前缀。
- 用户、助手、工具调用和工具结果的现有序列化顺序不变。

用量解析接受非负整数且排除布尔值。`input_tokens`、`cache_creation_input_tokens`、`cache_read_input_tokens` 分别读取后求和为总输入；缓存创建映射为 `cache_write_tokens`，缓存读取映射为 `cache_read_tokens`。流中后续用量快照覆盖当前请求已知状态。

### `mewcode/providers/openai.py`

**职责：** 保持稳定请求前缀、序列化动态系统消息并解析兼容端点缓存字段。

- 稳定 `SYSTEM` 保持请求 `messages` 的第一项。
- 每个 `SYSTEM_REMINDER` 序列化为紧随其后的 `role: system` 消息。
- 不发送 `cache_control`、缓存 TTL 或其他非标准缓存扩展。
- 首选解析 `usage.prompt_tokens_details.cached_tokens`；存在合法 `cache_write_tokens` 时解析写入值。
- 为 DeepSeek 等兼容端点兼容解析 `prompt_cache_hit_tokens` 作为读取值；标准明细优先。

正文、思考、工具调用和错误脱敏逻辑不改变。

### `mewcode/tools/registry.py`

**职责：** 在相关工具描述中局部强化工具选择和编辑规则。

- `find_files`、`search_code`：说明查找/搜索优先使用本工具而非 `run_command`。
- `read_file`：说明编辑既有文件前必须读取当前内容。
- `edit_file`：说明先读取并使用精确匹配，匹配失败不得改用整文件覆盖掩盖问题。
- `write_file`：说明主要用于新文件或明确整文件替换，既有文件必须先读取。
- `run_command`：说明不得替代已有专用文件、查找、搜索或编辑工具，且只依据实际结果声称成功。

### Smoke 与人工评估

新增 `scripts/smoke_prompt_cache.py`，使用现有配置与 Provider 工厂发送两轮固定、无副作用的短请求，打印 Provider、模型、总输入、输出、缓存读取和缓存写入；未知显示 `unknown`。脚本不读取或打印 API Key，不保证端点达到缓存门槛。

新增 `specs/06-system-prompt-cache/manual-evaluation.md`，固定五个场景、输入、观察项和旧/新提示结果表。首字等待时间定义为请求发出到第一个公开 `TEXT_DELTA` 的单调时钟差。文档只记录脱敏可观察行为，不保存隐藏思考。

## 模块交互

1. 应用启动时用默认 `PromptModule` 元组构建稳定 `SYSTEM_PROMPT`，Conversation 保存该稳定文本。
2. 用户发起任务，Agent Loop 选择执行模式或 Plan Mode 对应的稳定工具集。
3. 每轮请求前异步构造环境 reminder；Plan Mode 再按轮次构造模式 reminder。
4. Conversation 生成“稳定系统项 → 动态系统补充项 → 持久历史”的临时请求列表，不修改自身持久状态。
5. Provider 按协议序列化统一列表。Anthropic 在稳定工具和稳定系统块设置缓存控制；OpenAI-compatible 依赖相同请求前缀。
6. Provider 流式发布正文、工具、思考和最新用量快照。Agent Loop 保留本轮最后一个用量快照。
7. 若模型调用工具，既有执行器完成只读并发与副作用串行，结果写入持久历史；下一轮重新生成动态提醒但稳定前缀不变。
8. 本轮或任务结束时，Agent Loop 把该请求的最终用量快照合并到会话累计一次；TUI 仍只展示输入/输出总量。
9. 自动测试捕获请求体与事件；smoke 和 tmux 人工评估观察真实端点缓存字段与行为。

## 文件组织

```text
mewcode/
├── prompts.py                         # PromptModule、固定模块、装配器与 Plan reminder
├── environment.py                     # 动态环境与有界 Git 摘要
├── messages.py                        # SYSTEM_REMINDER 与缓存用量字段
├── conversation.py                    # 当前请求临时注入补充项
├── agent.py                           # 每轮注入、Plan 节奏与最终快照累计
├── tools/
│   └── registry.py                    # 关键工具描述双重强化
└── providers/
    ├── anthropic.py                   # 显式缓存块与 Anthropic 用量解析
    └── openai.py                      # 自动前缀缓存形态与兼容用量解析
scripts/
└── smoke_prompt_cache.py              # 真实端点缓存遥测 smoke
tests/
├── test_prompts.py                    # 模块顺序、确定性、提醒标签与频率
├── test_environment.py                # 环境字段、Git 降级、隐私与非阻塞
├── test_conversation.py               # 临时系统补充项与历史隔离
├── test_anthropic.py                  # cache_control、消息顺序与缓存用量
├── test_openai.py                     # 稳定前缀、系统消息与缓存用量
├── test_agent.py                      # 每轮注入、17 轮节奏与用量累计
└── test_app.py                        # TUI 用量展示保持不变
specs/06-system-prompt-cache/
├── spec.md
├── plan.md
├── task.md
├── checklist.md
├── manual-evaluation.md
└── acceptance-report.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 模块表示 | 不可变 `PromptModule` 数据类 | 字段清楚、易测试，避免运行期原地修改破坏确定性 |
| 排序 | 数值优先级降序 + 稳定声明顺序 | 满足插入新模块与同优先级确定性要求 |
| 可选模块 | 与固定模块同结构的空内容项 | 不在装配器中硬编码特殊分支，便于后续接入 |
| 动态上下文 | 独立 `SYSTEM_REMINDER` 类型 | 标签只是正文提示，真实角色由内部类型保证，用户不能伪造提升 |
| 历史策略 | 只在 `build_history` 返回值中注入 | 不污染持久历史，也不破坏工具调用与结果配对 |
| 环境采集 | 同步轻量字段 + `asyncio.to_thread` 有界 Git | 避免阻塞 TUI，同时不引入新依赖 |
| Git 输出 | 分支、clean/dirty、条目数 | 满足模型上下文需要并避免泄漏文件名与 remote URL |
| Provider 接口 | 保持 `stream(history, tools)` 不变 | 降低上层调用和测试迁移风险，缓存细节留在适配层 |
| Anthropic 缓存 | 最后工具定义与稳定 system block 显式断点 | 符合工具→系统→消息的前缀层级并排除动态块 |
| OpenAI 缓存 | 只保证稳定消息前缀，不发扩展参数 | 兼容官方与第三方端点，不假设非标准能力 |
| 缺失缓存指标 | `None`，不使用 0 | 区分“未提供”和“明确零命中” |
| 流式用量 | Provider 发布最新快照，Agent 每轮最终值只合并一次 | 避免多快照和多迭代重复计费 |
| Plan 节奏 | 轮次 1、6、11、16……完整，其余精简 | 与 Spec 固定频率一致且每个任务自然重置 |
| TUI 展示 | 不增加缓存字段 | 遵守范围边界，缓存只用于测试、smoke 和调试 |
| 评估 | 固定人工 A/B 模板，无自动分数 | 取得定性证据且不提前引入评测框架 |

## Spec 覆盖

| Spec | 设计归属 |
|---|---|
| F1 | `PromptModule`、默认模块元组、`build_system_prompt` |
| F2 | `mewcode/environment.py` 与每轮环境 reminder |
| F3 | Conversation 请求顺序、Anthropic cache_control、OpenAI 稳定前缀 |
| F4 | `TokenUsage`、两个 Provider 的解析与 Agent 最终快照累计 |
| F5 | 工具使用模块与 `ToolRegistry` 描述 |
| F6 | `SYSTEM_REMINDER`、临时历史注入和协议序列化 |
| F7 | `build_plan_mode_reminder`、Agent 轮次计数与既有双层只读限制 |
| F8 | 统一消息契约、共享提示/环境构造和跨 Provider 回归测试 |
| F9 | `manual-evaluation.md`、缓存 smoke 与 tmux 验收 |
