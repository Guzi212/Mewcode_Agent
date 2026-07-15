# MewCode Agent Loop Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `mewcode/agent.py` | Agent Loop、模式、停止条件、取消与事件输出 |
| 修改 | `mewcode/messages.py` | Provider 流事件、Agent 事件、Token 用量 |
| 修改 | `mewcode/conversation.py` | 合法模型回合与工具结果历史 |
| 修改 | `mewcode/prompts.py` | 默认、计划态与执行计划提示 |
| 修改 | `mewcode/config.py` | 最大迭代次数配置 |
| 修改 | `mewcode.yaml.example` | 最大迭代次数示例 |
| 修改 | `mewcode/providers/base.py` | 更新 Provider 流事件契约说明 |
| 修改 | `mewcode/providers/openai.py` | 合法历史序列化与 Token 用量解析 |
| 修改 | `mewcode/providers/anthropic.py` | 合法历史序列化与 Token 用量解析 |
| 修改 | `mewcode/tools/base.py` | 工具安全类别接口 |
| 修改 | `mewcode/tools/models.py` | 工具执行过程事件 |
| 修改 | `mewcode/tools/registry.py` | 工具分类、只读定义筛选 |
| 修改 | `mewcode/tools/executor.py` | 保序分批并发、过程事件和取消 |
| 修改 | `mewcode/tools/__init__.py` | 导出新增公共工具类型 |
| 修改 | `mewcode/tui/app.py` | 后台消费 Agent 事件、Esc 取消 |
| 修改 | `mewcode/tui/widgets.py` | 轮次和 Token 用量展示 |
| 新建 | `tests/test_agent.py` | Agent Loop、停止、Plan Mode、取消测试 |
| 新建 | `tests/test_tool_executor.py` | 分批并发、顺序和取消测试 |
| 修改 | `tests/test_conversation.py` | 合法跨轮历史测试 |
| 修改 | `tests/test_config.py` | 迭代上限配置测试 |
| 修改 | `tests/test_tool_registry.py` | 工具安全分类测试 |
| 修改 | `tests/test_openai.py` | OpenAI 用量及序列化测试 |
| 修改 | `tests/test_anthropic.py` | Anthropic 用量及序列化测试 |
| 修改 | `tests/test_app.py` | Agent 事件渲染、Token、轮次与取消测试 |

## T1：定义 Provider 用量与 Agent 事件契约

**文件：** `mewcode/messages.py`

**依赖：** 无

**步骤：**

1. 新增不可变的 `TokenUsage`，字段为可空的输入、输出 Token 数。
2. 为 `StreamEventKind` 增加 `USAGE`，为 `StreamEvent` 增加用量载荷和构造入口。
3. 定义 `AgentEventKind`、`StopReason` 和不可变的 `AgentEvent`，覆盖 spec F3 的全部事件。
4. 保留现有文本、工具调用、错误和结束事件的兼容构造方式。

**验证：** 运行 `python -m compileall -q mewcode/messages.py`，期望无输出且退出码为 0。

## T2：增加最大迭代次数配置

**文件：** `mewcode/config.py`、`mewcode.yaml.example`、`tests/test_config.py`

**依赖：** 无

**步骤：**

1. 为 `AppConfig` 增加默认值为 20 的 `agent_max_iterations`，保持现有构造调用兼容。
2. 从 YAML 顶层读取该值；仅正整数有效，缺失、布尔值、非整数和非正数均回退 20。
3. 在示例配置中说明该字段可选及默认值。
4. 添加默认、合法覆盖和各类非法值回退测试。

**验证：** 运行 `pytest -q tests/test_config.py`，期望全部通过。

## T3：更新 Agent 与 Plan Mode 提示词

**文件：** `mewcode/prompts.py`

**依赖：** 无

**步骤：**

1. 删除“工具结果后的最终答复不得再请求工具”的单轮限制。
2. 补充多轮自主执行、依据真实工具结果调整和完成后停止的约定。
3. 新增计划态提示，明确只调查和规划、禁止副作用操作。
4. 新增 `/do` 使用的内置执行指令，要求按上文计划开始执行。

**验证：** 运行 `python -m compileall -q mewcode/prompts.py`，期望退出码为 0；运行 `rg -n '不得再请求工具' mewcode/prompts.py`，期望无匹配。

## T4：为工具声明安全类别

**文件：** `mewcode/tools/base.py`、`mewcode/tools/registry.py`、`mewcode/tools/__init__.py`、`tests/test_tool_registry.py`

**依赖：** 无

**步骤：**

1. 定义只读和副作用两种工具安全类别，并加入 Tool 抽象接口。
2. 为六个内置工具登记正确类别：读文件、找文件、搜代码为只读，其余为副作用。
3. 为注册中心增加按名称查询类别和只导出只读定义的能力。
4. 更新测试用 FakeTool 并验证分类、筛选顺序和未知工具行为。

**验证：** 运行 `pytest -q tests/test_tool_registry.py`，期望全部通过。

## T5：重构合法对话历史表示

**文件：** `mewcode/messages.py`、`mewcode/conversation.py`、`tests/test_conversation.py`

**依赖：** T1

**步骤：**

1. 让一个 assistant 历史项同时承载文本和工具调用，移除必须拆成两个 assistant 回合的假设。
2. 提供记录普通文本回合、文本加工具调用回合和工具结果回合的明确入口。
3. 允许构建历史时传入当前模式的系统提示，同时保持纯文本上下文兼容入口。
4. 添加“前置文本 + 多工具调用 + 配对结果 + 下一轮答复”的顺序测试。

**验证：** 运行 `pytest -q tests/test_conversation.py`，期望全部通过。

## T6：调整 OpenAI 工具历史序列化

**文件：** `mewcode/providers/openai.py`、`tests/test_openai.py`

**依赖：** T5

**步骤：**

1. 把同一模型回合的文本与工具调用序列化为一个 assistant 消息。
2. 保持工具结果按调用顺序序列化为 tool 消息，并保留结构化错误内容。
3. 增加同时含前置文本和多个工具调用的请求体断言。
4. 保持现有纯文本和兼容端点工具调用测试通过。

**验证：** 运行 `pytest -q tests/test_openai.py -k 'serialize or tool or stream'`，期望相关测试全部通过。

## T7：调整 Anthropic 工具历史序列化

**文件：** `mewcode/providers/anthropic.py`、`tests/test_anthropic.py`

**依赖：** T5

**步骤：**

1. 把同一模型回合的文本块与 `tool_use` 块放入一个 assistant 内容列表。
2. 保持工具结果按调用顺序放入合法的 user `tool_result` 内容列表。
3. 增加同时含前置文本和多个工具调用的请求体断言。
4. 保持思考增量丢弃和纯文本请求测试通过。

**验证：** 运行 `pytest -q tests/test_anthropic.py -k 'serialize or tool or stream'`，期望相关测试全部通过。

## T8：解析 OpenAI 流式 Token 用量

**文件：** `mewcode/providers/openai.py`、`tests/test_openai.py`

**依赖：** T1、T6

**步骤：**

1. 在流式请求中声明包含用量；兼容端点忽略或不返回时仍正常工作。
2. 识别响应顶层用量对象并产生 `USAGE` 快照事件。
3. 输入或输出字段缺失、类型非法时使用 `None`，不得伪造数值。
4. 添加完整、缺失和无用量三类 SSE 测试。

**验证：** 运行 `pytest -q tests/test_openai.py -k usage`，期望全部通过。

## T9：解析 Anthropic 流式 Token 用量

**文件：** `mewcode/providers/anthropic.py`、`tests/test_anthropic.py`

**依赖：** T1、T7

**步骤：**

1. 从消息开始事件提取输入 Token，从消息增量或结束相关事件提取输出 Token。
2. 将每次已知值作为本轮快照产生 `USAGE` 事件。
3. 缺失或非法字段保持 `None`，不影响文本与工具事件。
4. 添加输入先到、输出后到和无用量三类 SSE 测试。

**验证：** 运行 `pytest -q tests/test_anthropic.py -k usage`，期望全部通过。

## T10：定义工具执行过程事件

**文件：** `mewcode/tools/models.py`、`mewcode/tools/__init__.py`

**依赖：** T4

**步骤：**

1. 定义工具执行开始和结束两种事件类型。
2. 事件携带调用，结束事件必须携带对应结果。
3. 提供清晰构造入口，避免执行器和 Agent 手写不完整事件。
4. 从工具包公共入口导出新增类型。

**验证：** 运行 `python -m compileall -q mewcode/tools`，期望退出码为 0。

## T11：实现连续只读并发与副作用串行

**文件：** `mewcode/tools/executor.py`、`tests/test_tool_executor.py`

**依赖：** T4、T10

**步骤：**

1. 将调用列表按原序切分为连续只读批和单个副作用批。
2. 只读批用独立任务并发执行，副作用调用逐个等待。
3. 每项实际启动前产生开始事件；批完成后按原始顺序产生结束事件。
4. 未知工具返回结构化失败，不阻断已登记工具。
5. 用可控屏障测试并发是否真实发生，并断言副作用调用不重叠、结果顺序稳定。

**验证：** 运行 `pytest -q tests/test_tool_executor.py -k 'batch or order or concurrent'`，期望全部通过。

## T12：实现执行器取消与资源清理

**文件：** `mewcode/tools/executor.py`、`tests/test_tool_executor.py`

**依赖：** T11

**步骤：**

1. 执行器同时观察取消信号和当前批任务。
2. 取消发生时取消并等待所有在途任务，吞掉预期的取消异常。
3. 为在途及未开始调用生成 `cancelled` 结果，且每个调用恰有一个结束事件。
4. 添加取消并发批、取消副作用调用、开始前已取消和无残留任务测试。

**验证：** 运行 `pytest -q tests/test_tool_executor.py -k cancel`，期望全部通过且无 asyncio 挂起任务告警。

## T13：建立 AgentLoop 命令解析与运行骨架

**文件：** `mewcode/agent.py`、`tests/test_agent.py`

**依赖：** T1、T2、T3、T4、T5

**步骤：**

1. 建立 AgentLoop 依赖注入、单任务互斥、会话状态和取消事件。
2. 解析普通输入、`/plan <任务>`、`/do` 和 `/do <任务>`，去除命令前缀后再写入有效任务。
3. `/do` 无计划时产生用户消息、可恢复错误和结束事件，但不调用 Provider。
4. 拒绝同一 AgentLoop 同时启动第二个任务，并以可恢复错误结束。
5. 添加命令解析、空任务和互斥测试。

**验证：** 运行 `pytest -q tests/test_agent.py -k 'command or busy or empty'`，期望全部通过。

## T14：实现自然完成与多轮工具循环

**文件：** `mewcode/agent.py`、`tests/test_agent.py`

**依赖：** T6、T7、T11、T13

**步骤：**

1. 每轮发布迭代事件并消费 Provider 流，实时转发文本同时累计整轮文本和完整工具调用。
2. 无工具调用时记录最终模型回合，发布轮次结束和自然完成事件。
3. 有工具调用时先记录模型回合，再消费执行器事件并记录按序配对的工具结果，进入下一轮。
4. 添加纯文本单轮、两轮工具调用后完成、前置文本加工具调用和单项失败继续测试。

**验证：** 运行 `pytest -q tests/test_agent.py -k 'complete or multiround or preamble'`，期望全部通过。

## T15：实现流错误与迭代上限停止

**文件：** `mewcode/agent.py`、`tests/test_agent.py`

**依赖：** T14

**步骤：**

1. Provider 产生错误时发布 Agent 错误和 `STREAM_ERROR` 结束事件，不再进入下一轮。
2. 保留流错误前已到达的文本为合法模型回合。
3. 在启动下一轮前检查迭代上限，达到上限后发布提示和 `ITERATION_LIMIT`。
4. 验证停止后没有额外 Provider 调用或工具执行，并可继续启动下一任务。

**验证：** 运行 `pytest -q tests/test_agent.py -k 'stream_error or iteration_limit'`，期望全部通过。

## T16：实现连续全未知工具停止

**文件：** `mewcode/agent.py`、`tests/test_agent.py`

**依赖：** T14

**步骤：**

1. 每轮调用全都未知时计数加一，并把未知工具结构化结果正常回灌。
2. 任一轮含已登记工具时把计数清零。
3. 第三个连续全未知轮次完成历史配对后，以 `UNKNOWN_TOOL_LIMIT` 结束且不请求下一轮。
4. 添加连续三轮、混合已知/未知和中途清零测试。

**验证：** 运行 `pytest -q tests/test_agent.py -k unknown`，期望全部通过。

## T17：实现 Token 快照汇总与累计事件

**文件：** `mewcode/agent.py`、`tests/test_agent.py`

**依赖：** T8、T9、T14

**步骤：**

1. 每轮保存最新输入、输出 Token 快照，不把同轮增量重复累计。
2. 对已提供字段在轮末累计到会话总量；不可用字段保持不可用语义。
3. 收到快照时发布当前会话累计加本轮快照的 `USAGE_UPDATED`。
4. 添加同轮多快照、多轮累计、部分字段缺失和完全无用量测试。

**验证：** 运行 `pytest -q tests/test_agent.py -k usage`，期望全部通过。

## T18：实现 Agent 取消与合法历史补齐

**文件：** `mewcode/agent.py`、`tests/test_agent.py`

**依赖：** T12、T14

**步骤：**

1. `cancel_current()` 只设置当前任务取消信号并返回是否确有运行任务。
2. 流式收集阶段同时等待 Provider 和取消；取消时停止内部 Provider 工作并正常收束 Agent 事件流。
3. 工具阶段消费执行器生成的取消结果，确保全部已记录调用均有配对结果。
4. 发布取消提示、轮次结束和 `CANCELLED`，并允许下一任务继续使用合法历史。
5. 添加流式中取消、并发工具中取消、取消后续聊和无残留任务测试。

**验证：** 运行 `pytest -q tests/test_agent.py -k cancel`，期望全部通过且无 asyncio 资源告警。

## T19：实现 Plan Mode 与 `/do`

**文件：** `mewcode/agent.py`、`tests/test_agent.py`

**依赖：** T3、T4、T14、T18

**步骤：**

1. `/plan` 的每轮请求叠加计划提示并只发送只读工具定义。
2. 拦截计划模式下模型主动请求的副作用工具，返回 `tool_not_allowed_in_plan`，不得调用执行器。
3. 计划自然完成时保存最终文本为会话内待执行计划。
4. `/do` 追加内置执行指令并用完整工具集立即执行保存计划；`/do <任务>` 直接执行新任务。
5. 添加只读定义、违规拦截、计划保存、无参执行和显式任务执行测试。

**验证：** 运行 `pytest -q tests/test_agent.py -k plan`，期望全部通过。

## T20：扩展轮次与 Token 展示组件

**文件：** `mewcode/tui/widgets.py`、`tests/test_app.py`

**依赖：** T1

**步骤：**

1. 为等待指示器增加当前迭代轮次显示，并保留计时和总耗时功能。
2. 为状态栏增加累计输入、输出 Token 展示及不可用状态。
3. 保持 provider 与 model 信息清晰且在窄终端不覆盖关键状态。
4. 添加组件更新断言。

**验证：** 运行 `pytest -q tests/test_app.py -k 'usage or iteration or status'`，期望相关测试全部通过。

## T21：将 TUI 接入 Agent 事件流

**文件：** `mewcode/tui/app.py`、`tests/test_app.py`

**依赖：** T14、T17、T20

**步骤：**

1. 在应用装配时创建或注入 AgentLoop，移除 TUI 内两次 Provider 请求和直接执行工具的编排。
2. 提交输入后使用 Textual 后台 worker 消费 `AgentLoop.run()`，保持界面消息循环响应。
3. 按 USER_MESSAGE、ITERATION_STARTED、TEXT_DELTA、USAGE_UPDATED、ERROR 和 TASK_FINISHED 更新界面。
4. 每轮文本使用独立 AssistantMessage，结束时正确 markdown 定型；任务结束恢复输入与焦点。
5. 更新 FakeAgent 测试，验证纯文本、错误、用量、轮次和完成路径。

**验证：** 运行 `pytest -q tests/test_app.py -k 'stream or error or usage or iteration'`，期望相关测试全部通过。

## T22：渲染多轮工具事件并保持顺序

**文件：** `mewcode/tui/app.py`、`mewcode/tui/widgets.py`、`tests/test_app.py`

**依赖：** T21

**步骤：**

1. TOOL_STARTED 时按调用 ID 建立工具行，TOOL_FINISHED 时完成对应工具行。
2. 结果失败时显示可区分错误，成功摘要保留在 scrollback。
3. ROUND_FINISHED 时收束当前模型文本，不打乱跨轮前置文本、工具行和最终答复顺序。
4. 添加并发只读批、串行副作用批和多轮前置文本的显示顺序测试。

**验证：** 运行 `pytest -q tests/test_app.py -k 'tool or order or scroll'`，期望相关测试全部通过。

## T23：接入 Esc 取消并保持 Ctrl+C 退出

**文件：** `mewcode/tui/app.py`、`mewcode/tui/widgets.py`、`tests/test_app.py`

**依赖：** T18、T21

**步骤：**

1. 增加高优先级 Esc 绑定，仅在任务运行时调用 `cancel_current()`。
2. 立即把等待区更新为“正在取消”，等待 Agent 的取消结束事件后恢复输入。
3. 空闲时 Esc 不改变会话；`Ctrl+C` 始终保持退出程序。
4. 添加运行时 Esc、空闲 Esc、取消后继续输入和 Ctrl+C 回归测试。

**验证：** 运行 `pytest -q tests/test_app.py -k 'cancel or ctrl_c or escape'`，期望相关测试全部通过。

## T24：运行跨层回归并修正接口遗漏

**文件：** 上述所有实现与测试文件

**依赖：** T2–T23

**步骤：**

1. 运行 Provider、Conversation、Registry、Executor、Agent 和 TUI 聚焦测试。
2. 修复类型、导出、旧调用方和测试替身未同步的问题，不跳过失败测试。
3. 运行全量测试，确认沙箱、文件工具、命令工具、配置和 CLI 行为无回归。
4. 运行字节码编译检查并确认没有未捕获异常或资源警告。

**验证：** 运行 `pytest -q` 与 `python -m compileall -q mewcode tests`，期望全部成功。

## T25：执行 tmux 端到端冒烟

**文件：** 不修改文件；按 `checklist.md` 记录结果

**依赖：** T24、已批准的 `checklist.md`

**步骤：**

1. 在 tmux 中启动 MewCode，并保留可观察的终端输出。
2. 输入一个至少需要两轮工具调用的真实任务，观察自动推进、轮次、工具结果和最终答复。
3. 执行一次 `/plan <任务>`，确认只读调查和计划输出；再输入 `/do`，确认完整工具执行。
4. 运行一个可取消任务并按 Esc，确认取消提示、输入恢复和后续对话可用。

**验证：** 对照已批准的 `checklist.md`，三类场景全部得到可观测证据；任一项失败则返回对应任务修复并重测。

## 执行顺序

```text
T1 ─┬─→ T5 ─→ T6 ─→ T8 ─┐
    │        └→ T7 ─→ T9 ├─→ T14 ─→ T15 ─→ T16 ─→ T17 ─→ T18 ─→ T19
T2 ─┤                    │                                      │
T3 ─┤                    │                                      ├─→ T21 ─→ T22 ─→ T23
T4 ─┴─→ T10 ─→ T11 ─→ T12 ─→ T13 ───────────────────────────────┘
T1 ───────────────────────────────────────────────────────→ T20 ─┘
T2–T23 ─→ T24 ─→ T25
```
