# MewCode 工具系统 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `mewcode/messages.py` | 协议无关会话项、工具调用/结果、流事件 |
| 修改 | `mewcode/conversation.py` | 工具调用与结果历史维护 |
| 修改 | `mewcode/prompts.py` | Agent、工具、沙箱和单轮限制提示词 |
| 新建 | `mewcode/tools/__init__.py` | 工具公共导出和默认注册中心 |
| 新建 | `mewcode/tools/models.py` | 工具定义、调用、结果与错误数据模型 |
| 新建 | `mewcode/tools/base.py` | 工具抽象与通用参数验证 |
| 新建 | `mewcode/tools/registry.py` | 工具注册、查找和六项工具登记 |
| 新建 | `mewcode/tools/executor.py` | 顺序执行、超时、审批协调与错误规范化 |
| 新建 | `mewcode/tools/files.py` | 读、写、唯一替换和文本输出限制 |
| 新建 | `mewcode/tools/search.py` | glob 和代码内容搜索 |
| 新建 | `mewcode/tools/command.py` | 命令执行、输出采集、进程组终止 |
| 新建 | `mewcode/sandbox/__init__.py` | 沙箱工厂及公共导出 |
| 新建 | `mewcode/sandbox/models.py` | 权限、授权和沙箱请求数据模型 |
| 新建 | `mewcode/sandbox/base.py` | 沙箱后端抽象与工厂 |
| 新建 | `mewcode/sandbox/permissions.py` | 真实路径判断和会话授权存储 |
| 新建 | `mewcode/sandbox/macos.py` | Seatbelt 后端 |
| 新建 | `mewcode/sandbox/linux.py` | Bubblewrap 后端 |
| 新建 | `mewcode/sandbox/windows.py` | Windows Sandbox 后端 |
| 新建 | `mewcode/tool_worker.py` | 沙箱内 JSON Lines 工作进程 |
| 修改 | `mewcode/providers/base.py` | 扩展工具感知的流式接口 |
| 修改 | `mewcode/providers/openai.py` | OpenAI 工具定义、历史序列化、流式调用拼接 |
| 修改 | `mewcode/providers/anthropic.py` | Anthropic 工具定义、历史序列化、流式调用拼接 |
| 修改 | `mewcode/tui/widgets.py` | 工具行与结果摘要组件 |
| 修改 | `mewcode/tui/screens.py` | 外部路径授权弹窗 |
| 修改 | `mewcode/tui/app.py` | 两段请求、工具批次和单轮上限编排 |
| 新建/修改 | `tests/test_*.py` | 工具、沙箱、协议、会话和 TUI 测试 |

## T1：建立工具与会话数据契约

**文件：** `mewcode/tools/models.py`、`mewcode/messages.py`、`mewcode/conversation.py`、`tests/test_conversation.py`

**依赖：** 无

**步骤：**

1. 定义 `ToolDefinition`、`ToolCall`、`ToolError`、`ToolResult`，含调用关联 ID、成功状态、模型输出、TUI 摘要、截断标志与错误码。
2. 将现有文本消息模型演进为 `ConversationItem`，支持 system、user、assistant、工具调用批次和工具结果批次。
3. 扩展 `StreamEventKind` 为可携带完整 `ToolCall` 的工具调用事件，保留文本、错误和完成事件的既有行为。
4. 扩展 `Conversation` 的追加和构建方法，使用户文本、助手文本、调用批次和结果批次按时间顺序保存。
5. 增加历史顺序、调用 ID 关联和纯文本历史兼容性测试。

**验证：** `pytest -q tests/test_conversation.py` 通过；既有纯文本上下文仍为 system、user、assistant 的正确顺序。

## T2：实现工具抽象与注册中心

**文件：** `mewcode/tools/base.py`、`mewcode/tools/registry.py`、`mewcode/tools/__init__.py`、`tests/test_tool_registry.py`

**依赖：** T1

**步骤：**

1. 定义 `Tool` 抽象，包含工具定义、访问需求计算和异步执行入口。
2. 实现 `ToolRegistry.register()`、`get()` 和 `definitions()`，拒绝重复名称并保持登记顺序。
3. 声明六项固定工具的名称、中文描述和 JSON Schema；每项 Schema 仅接受其必要参数，禁止未声明字段。
4. 提供 `build_default_registry()` 并在包入口导出公共类型。
5. 编写登记顺序、按名查找、未知名称和六项 Schema 完整性的测试。

**验证：** `pytest -q tests/test_tool_registry.py` 通过，导出的工具定义恰有六项。

## T3：实现权限模型与会话授权存储

**文件：** `mewcode/sandbox/models.py`、`mewcode/sandbox/permissions.py`、`tests/test_sandbox_permissions.py`

**依赖：** T1

**步骤：**

1. 定义读、写、执行权限模式，以及拒绝、单次、会话三种审批范围。
2. 定义访问请求、授权记录和沙箱请求，记录工具名、真实目标路径、权限与可选命令摘要。
3. 实现真实路径规范化、工作目录默认权限和符号链接目标检查。
4. 实现内存会话授权存储：单次授权仅供一项请求消费，会话授权按真实路径与权限模式匹配，应用退出即丢失。
5. 覆盖文件/目录授权、读写不可升级、相对路径逃逸、符号链接逃逸、拒绝与授权失效测试。

**验证：** `pytest -q tests/test_sandbox_permissions.py` 通过，未经授权的工作目录外路径不匹配有效权限。

## T4：定义沙箱抽象、工厂与失败关闭行为

**文件：** `mewcode/sandbox/base.py`、`mewcode/sandbox/__init__.py`、`tests/test_sandbox_backends.py`

**依赖：** T3

**步骤：**

1. 定义 `Sandbox.run()` 抽象、沙箱不可用异常和 `SandboxFactory`。
2. 根据操作系统和可用后端选择 macOS、Linux/WSL 或 Windows 后端。
3. 为不支持的平台、缺失依赖和后端启动失败定义统一的结构化失败结果路径。
4. 确保工厂不返回无隔离的本地执行器作为回退。
5. 以替身后端测试平台选择和失败关闭语义。

**验证：** `pytest -q tests/test_sandbox_backends.py` 通过；无后端时结果码为 `sandbox_unavailable`。

## T5：实现沙箱工作进程协议与环境清洗

**文件：** `mewcode/tool_worker.py`、`mewcode/sandbox/base.py`、`tests/test_tool_worker.py`、`tests/test_sandbox_backends.py`

**依赖：** T1、T4

**步骤：**

1. 定义主进程与工作进程之间的一行 JSON 输入/一行 JSON 输出协议。
2. 在工作进程中反序列化已验证的工具请求，分派到纯工具操作函数，并捕获所有异常为 `ToolResult`。
3. 在沙箱启动参数中仅传递最小运行环境；显式剔除 API Key、代理凭据及其他敏感变量。
4. 限制工作进程网络，并确保命令子进程继承同一沙箱和清洗后的环境。
5. 测试协议往返、格式错误、异常转换和敏感环境变量不继承。

**验证：** `pytest -q tests/test_tool_worker.py tests/test_sandbox_backends.py` 通过；测试工作进程看不到注入的假 API Key。

## T6：实现 macOS Seatbelt 沙箱后端

**文件：** `mewcode/sandbox/macos.py`、`tests/test_sandbox_backends.py`

**依赖：** T4、T5

**步骤：**

1. 根据默认工作目录、临时目录和本次授权生成最小 Seatbelt 规则。
2. 允许 Python 运行时依赖只读、授权路径按读/写模式挂载，关闭网络。
3. 通过 Seatbelt 启动工作进程，并将超时、退出状态和协议错误归一化。
4. 验证默认边界、额外单文件读授权、目录写授权和未授权路径拒绝。

**验证：** 在 macOS 上运行 `pytest -q tests/test_sandbox_backends.py`；工作进程可访问授权文件，不能访问未授权临时外部文件。

## T7：实现 Linux/WSL Bubblewrap 与 Windows 后端

**文件：** `mewcode/sandbox/linux.py`、`mewcode/sandbox/windows.py`、`tests/test_sandbox_backends.py`

**依赖：** T4、T5

**步骤：**

1. 用 Bubblewrap 构建 Linux/WSL 的只读运行时、可写临时目录、工作目录和临时授权路径边界，并禁用网络命名空间访问。
2. 用原生 Windows Sandbox 构建等价的最小路径与网络边界。
3. 将后端依赖缺失、启动失败和无法施加规则统一转换为不可用错误。
4. 以命令组装单测验证三类授权路径和失败关闭规则；在对应平台运行实际后端验证。

**验证：** `pytest -q tests/test_sandbox_backends.py` 通过；每个平台后端缺失时均拒绝执行，不回退到宿主执行。

## T8：实现文件工具及原子写入

**文件：** `mewcode/tools/files.py`、`tests/test_tools_files.py`

**依赖：** T1、T5

**步骤：**

1. 实现读文件：仅处理 UTF-8 文本、添加行号、应用统一输出行数和字符数上限。
2. 实现写文件：创建缺失父目录，在同目录临时写入后原子替换目标文件。
3. 实现改文件：计数原文出现次数，仅在恰好一次时替换；零次和多次均返回实际匹配次数且不写入。
4. 为不存在、目录目标、二进制/解码失败、权限失败和截断状态生成结构化结果。
5. 覆盖读成功/失败、写新文件/覆盖/嵌套目录、原子失败保持原文件、三种编辑匹配情况与截断测试。

**验证：** `pytest -q tests/test_tools_files.py` 通过。

## T9：实现查找与代码搜索工具

**文件：** `mewcode/tools/search.py`、`tests/test_tools_search.py`

**依赖：** T1、T5

**步骤：**

1. 实现 glob 文件查找，返回相对路径、跳过目录和超出结果上限的条目。
2. 实现文本搜索，支持可选路径范围，返回文件、行号和命中文本。
3. 跳过二进制或无法解码内容，并将无结果、无效模式和范围不可访问转成结构化结果。
4. 为命中数量和输出文本应用统一截断标志。
5. 覆盖 `**/*.py` 查找、关键字搜索、可选范围、无结果、二进制跳过和海量结果截断。

**验证：** `pytest -q tests/test_tools_search.py` 通过。

## T10：实现命令工具与进程超时

**文件：** `mewcode/tools/command.py`、`tests/test_tools_command.py`

**依赖：** T1、T5

**步骤：**

1. 实现命令工具的参数验证和默认工作目录；明确工作目录参数触发外部目录授权检查。
2. 在工作进程内启动 shell 命令、采集标准输出/标准错误/退出码，并限制输出体量。
3. 对超时命令终止整个进程组，等待回收后返回 `timeout` 结构化结果。
4. 将非零退出保留为可诊断结果而非未捕获异常。
5. 覆盖成功命令、标准错误、非零退出、超时、长输出截断和子进程回收测试。

**验证：** `pytest -q tests/test_tools_command.py` 通过；超时测试在规定时间内结束且无遗留子进程。

## T11：实现执行器与六项工具装配

**文件：** `mewcode/tools/executor.py`、`mewcode/tools/registry.py`、`mewcode/tools/__init__.py`、`tests/test_tool_registry.py`

**依赖：** T2、T3、T5、T8、T9、T10

**步骤：**

1. 将六项工具接入默认注册中心，并为每项工具实现输入参数校验和访问请求计算。
2. 实现 `ToolExecutor.execute_batch()`：按调用顺序查找、审批、启动沙箱、收集结果。
3. 将未知工具、参数错误、审批拒绝、沙箱错误、超时和内部异常转换为统一结果，不阻断后续调用。
4. 注入可替换审批回调，供 TUI 和单测分别使用。
5. 增加同批多调用、首项失败后续仍运行、一次授权消费、会话授权复用和未知工具测试。

**验证：** `pytest -q tests/test_tool_registry.py tests/test_tools_files.py tests/test_tools_search.py tests/test_tools_command.py` 通过。

## T12：更新 system prompt 与 Provider 统一接口

**文件：** `mewcode/prompts.py`、`mewcode/providers/base.py`、`tests/test_conversation.py`

**依赖：** T1、T2

**步骤：**

1. 移除“仅纯对话、不能调用工具”的旧提示。
2. 写入 Agent 角色、六项工具、真实结果优先、外部路径审批、系统级沙箱和第二次回复不应再调用工具的约定。
3. 将 `Provider.stream()` 参数改为协议无关历史与工具定义，并更新抽象文档。
4. 确保未发生工具调用时的历史内容与原有对话行为等价。

**验证：** `pytest -q tests/test_conversation.py tests/test_factory.py` 通过；提示词不再声称无法调用工具。

## T13：实现 OpenAI 工具调用与历史序列化

**文件：** `mewcode/providers/openai.py`、`tests/test_openai.py`

**依赖：** T1、T2、T12

**步骤：**

1. 将统一工具定义转换为 OpenAI function 工具数组，并随首次及第二次请求发送。
2. 将统一历史转换为 system/user/assistant、assistant tool calls 和 tool result 消息。
3. 按 SSE 中工具调用索引累积调用 ID、函数名与 arguments JSON 碎片；在完成后解析并产出完整 `TOOL_CALL` 事件。
4. 保持正文增量、`reasoning_content` 丢弃、HTTP/网络错误和 `[DONE]` 处理。
5. 覆盖请求体工具定义、碎片拼接、工具结果回灌、多个调用、非法 JSON 结构化错误及纯文本回归测试。

**验证：** `pytest -q tests/test_openai.py` 通过；抓取请求体可见六项工具定义及正确的 tool result 消息。

## T14：实现 Anthropic 工具调用与历史序列化

**文件：** `mewcode/providers/anthropic.py`、`tests/test_anthropic.py`

**依赖：** T1、T2、T12

**步骤：**

1. 将统一工具定义转换为 Anthropic 工具数组，并随两次请求发送。
2. 将统一历史转换为 system 字段、文本内容块、assistant tool_use 内容块和 user tool_result 内容块。
3. 根据 `content_block_start`、`input_json_delta`、`content_block_stop` 拼接输入 JSON 并产出完整 `TOOL_CALL` 事件。
4. 保持 text_delta、thinking_delta 丢弃、message_stop 和错误事件的既有语义。
5. 覆盖请求工具定义、分片 JSON、工具结果回灌、多个工具调用、非法 JSON 和纯文本回归测试。

**验证：** `pytest -q tests/test_anthropic.py` 通过；断言请求包含正确工具 schema 与 tool_result 内容块。

## T15：实现工具行与授权选择界面

**文件：** `mewcode/tui/widgets.py`、`mewcode/tui/screens.py`、`tests/test_screens.py`、`tests/test_app.py`

**依赖：** T1、T3

**步骤：**

1. 新增工具行组件，首次显示工具名和关键参数，执行完成后显示成功、失败或截断摘要。
2. 为工具行和错误结果添加可区分样式，并确保其可进入 scrollback 历史。
3. 新增 modal 授权屏，展示工具、真实路径、读/写/执行权限和命令摘要。
4. 使用方向键和 Enter 实现“拒绝、仅本次允许、本会话允许”，默认焦点为拒绝。
5. 增加键盘选择、屏幕返回值、工具行更新和长摘要展示测试。

**验证：** `pytest -q tests/test_screens.py tests/test_app.py` 通过。

## T16：接入首次请求、工具批次与一次续答

**文件：** `mewcode/tui/app.py`、`tests/test_app.py`

**依赖：** T1、T11、T13、T14、T15

**步骤：**

1. 在应用装配默认注册中心、沙箱和执行器，并以可注入方式保留现有测试替身能力。
2. 将一轮提交拆为首次流式响应、工具批次执行和第二次最终流式响应三段；在每段正确维护计时、忙碌状态、滚动和输入禁用。
3. 首次响应收集完整工具调用并写入会话；执行后把全部结果写入会话；第二次响应只渲染最终文本。
4. 第二次响应出现 `TOOL_CALL` 时挂载单轮上限错误，不执行、不再次调用 Provider。
5. 覆盖“读文件并总结”闭环、多工具顺序批次、首项失败后继续、最终请求工具上限、错误恢复和纯文本对话回归。

**验证：** `pytest -q tests/test_app.py` 通过；断言工具闭环时 Provider 恰被调用两次。

## T17：运行单元与协议集成回归

**文件：** 全部 `tests/`、按需修正实现文件

**依赖：** T1–T16

**步骤：**

1. 运行全部测试，修复数据契约变更造成的既有测试失败。
2. 分别以模拟 Anthropic 与 OpenAI SSE 跑通工具定义注入、调用分片、结果回灌和最终文本答复。
3. 验证文件操作、搜索、命令、授权与沙箱不可用路径均无未捕获异常。
4. 检查错误输出、工具结果和测试断言中不含 API Key 或其他敏感配置。

**验证：** `pytest -q` 全部通过。

## T18：进行 tmux 端到端验收准备

**文件：** `specs/02-tool-system/checklist.md`（将在验收设计阶段生成）、测试用临时工作目录

**依赖：** T17、已批准的 checklist

**步骤：**

1. 在临时目录创建可读取、可修改、可搜索的样例文本和一个工作目录外样例文件。
2. 在 tmux 启动 MewCode，选择 Provider，输入真实的“读取并总结”请求，观察工具行、结果回灌和最终答复。
3. 输入工作目录外文件请求，分别验证拒绝、仅本次允许和本会话允许的方向键流程。
4. 输入命令失败和唯一替换失败请求，确认结构化错误后仍可继续对话。
5. 按已批准 checklist 记录实际观察结果。

**验证：** tmux 中完整链路可观察，且所有 checklist 条目均有命令输出或界面证据。

## 执行顺序

```text
T1 → T2 ───────────────┐
 │                      ├→ T11 ─┐
 ├→ T3 → T4 → T5 → T6 ┤         │
 │                   └→ T7 ┘     │
 ├→ T8 ──────────────────────────┤
 ├→ T9 ──────────────────────────┤
 └→ T10 ─────────────────────────┘

T1 → T12 → T13 ─┐
               ├→ T16 → T17 → T18
          T14 ─┘
T1 + T3 → T15 ─┘
```
