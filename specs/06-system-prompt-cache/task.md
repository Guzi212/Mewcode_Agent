# 系统提示工程化 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `mewcode/prompts.py` | 模块化稳定提示、系统提醒标签和 Plan 轮次策略 |
| 新建 | `mewcode/environment.py` | 动态环境信息和有界 Git 摘要 |
| 修改 | `mewcode/messages.py` | 系统补充项类型与缓存读写用量 |
| 修改 | `mewcode/conversation.py` | 当前请求临时注入系统补充项 |
| 修改 | `mewcode/agent.py` | 每轮环境/Plan 注入和最终用量快照累计 |
| 修改 | `mewcode/providers/anthropic.py` | 显式缓存块与缓存用量解析 |
| 修改 | `mewcode/providers/openai.py` | 动态系统消息、稳定前缀和缓存用量解析 |
| 修改 | `mewcode/tools/registry.py` | 相关工具描述双重强化 |
| 修改 | `mewcode/sandbox/macos.py` | 修复 tmux 验收暴露的严格 JSONL worker 换行协议缺陷 |
| 新建 | `scripts/smoke_prompt_cache.py` | 真实端点两轮缓存遥测 smoke |
| 新建 | `tests/test_prompts.py` | 模块装配、确定性、提醒标签和 Plan 频率测试 |
| 新建 | `tests/test_environment.py` | 环境字段、Git 降级、隐私和异步边界测试 |
| 修改 | `tests/test_conversation.py` | 补充项临时性、角色和顺序测试 |
| 修改 | `tests/test_anthropic.py` | Anthropic 请求结构和缓存用量测试 |
| 修改 | `tests/test_openai.py` | OpenAI-compatible 请求结构和缓存用量测试 |
| 修改 | `tests/test_agent.py` | 每轮注入、17 轮节奏和用量累计测试 |
| 修改 | `tests/test_app.py` | TUI 不展示缓存明细的回归测试 |
| 修改 | `tests/test_sandbox_backends.py` | macOS worker 请求必须是单行 JSONL 的回归测试 |
| 新建 | `specs/06-system-prompt-cache/manual-evaluation.md` | 五个人工 A/B 场景与结果模板 |
| 新建 | `specs/06-system-prompt-cache/acceptance-report.md` | checklist 实际验收证据 |

## T1：记录开发基线

**文件：** 不修改实现文件

**依赖：** 无

**步骤：**

1. 运行 `git status --short`，记录并保护现有未提交文件。
2. 运行完整 Python 测试，记录通过、失败和跳过数量。
3. 运行 `python -m compileall` 与 `git diff --check`，确认开发前语法和文档基线。
4. 检查项目是否配置 Ruff 或 mypy；没有配置时只记录，不新增依赖或伪报通过。

**验证：** `.venv/bin/python -m pytest -q -ra -p no:cacheprovider` 与 `.venv/bin/python -m compileall -q mewcode tests` 退出 0；已有未提交文件清单写入开发记录。

## T2：建立模块装配失败测试

**文件：** `tests/test_prompts.py`

**依赖：** T1

**步骤：**

1. 添加固定七模块严格顺序和单空行断言。
2. 添加三个可选空槽自动跳过且首尾无空白断言。
3. 添加同优先级保持声明顺序和新增模块无需改装配器断言。
4. 添加两次构建逐字节相同，动态环境与轮次不属于稳定提示断言。
5. 添加 `<system-reminder>` 标签和第 1、6、11、16 轮完整提醒断言。
6. 运行测试，确认失败原因是接口尚未实现。

**验证：** `.venv/bin/python -m pytest tests/test_prompts.py -q -p no:cacheprovider` 稳定失败在缺失的模块化接口，不出现夹具或导入路径错误。

## T3：实现稳定系统提示模块

**文件：** `mewcode/prompts.py`、`tests/test_prompts.py`

**依赖：** T2

**步骤：**

1. 定义不可变 `PromptModule` 和默认模块元组。
2. 写入身份、系统约束、任务模式、动作执行、工具使用、语气风格、文本输出七个固定模块。
3. 在固定模块后声明自定义指令、已激活 Skill、长期记忆三个空槽。
4. 实现过滤空内容、优先级降序、同优先级稳定顺序和单空行连接。
5. 用默认构建结果提供兼容 `SYSTEM_PROMPT`，保留执行计划提示兼容常量。
6. 实现统一 reminder 标签和 Plan Mode 完整/精简轮次策略。

**验证：** `.venv/bin/python -m pytest tests/test_prompts.py -q -p no:cacheprovider` 全部通过。

## T4：建立环境采集失败测试

**文件：** `tests/test_environment.py`

**依赖：** T1、T3

**步骤：**

1. 添加工作目录、平台架构、Shell、日期、版本、Provider 和模型字段测试。
2. 在临时 Git 仓库中添加分支与 dirty 摘要测试，断言不含文件名、remote URL 和文件内容。
3. 添加非 Git 目录、Git 命令缺失、超时和非法输出的降级测试。
4. 设置哨兵 API Key 与敏感环境变量，断言 reminder 不包含其值。
5. 用事件循环心跳或线程替身断言 Git 子进程不直接阻塞异步调用。
6. 运行测试，确认失败来自环境模块尚未实现。

**验证：** `.venv/bin/python -m pytest tests/test_environment.py -q -p no:cacheprovider` 稳定失败在缺失接口。

## T5：实现动态环境 reminder

**文件：** `mewcode/environment.py`、`tests/test_environment.py`

**依赖：** T4

**步骤：**

1. 实现平台、架构、Shell、日期、版本、Provider 和模型的安全格式化。
2. 实现使用参数数组、非交互环境和 1 秒超时的 Git 状态采集。
3. 只从 Git 输出提取仓库状态、分支和 clean/dirty 条目数。
4. 用 `asyncio.to_thread` 执行 Git 采集并在字段边界捕获异常。
5. 把环境正文交给统一 `build_system_reminder` 包装。
6. 保证测试注入的 `cwd` 与 `today` 不影响稳定系统提示。

**验证：** `.venv/bin/python -m pytest tests/test_environment.py tests/test_prompts.py -q -p no:cacheprovider` 全部通过。

## T6：建立临时系统补充项失败测试

**文件：** `tests/test_conversation.py`

**依赖：** T3

**步骤：**

1. 添加请求顺序“稳定系统 → 多个 reminder → 持久历史”断言。
2. 添加连续两次构造不同 reminder 后持久 `items` 和公开 `messages` 不变断言。
3. 添加用户文本包含 `<system-reminder>` 时类型仍为 `USER` 的断言。
4. 添加工具调用与结果在 reminder 注入后仍相邻配对的断言。
5. 运行测试，确认失败来自补充项类型与接口尚未实现。

**验证：** `.venv/bin/python -m pytest tests/test_conversation.py -q -p no:cacheprovider` 只在新增行为上失败。

## T7：扩展消息、会话和用量契约

**文件：** `mewcode/messages.py`、`mewcode/conversation.py`、`tests/test_conversation.py`

**依赖：** T6

**步骤：**

1. 增加 `ConversationItemKind.SYSTEM_REMINDER` 与可信构造入口。
2. 为 `TokenUsage` 增加默认 `None` 的缓存读取和写入字段。
3. 扩展 `Conversation.build_history` 接收只读 reminder 序列。
4. 仅在返回列表创建临时补充项，不修改 Conversation 持久状态。
5. 保持原 `build_history(system_prompt)` 和既有 TokenUsage 构造兼容。
6. 验证用户同名标签不改变内部类型。

**验证：** `.venv/bin/python -m pytest tests/test_conversation.py -q -p no:cacheprovider` 全部通过，并由该测试覆盖新增消息与用量数据契约。

## T8：建立 Anthropic 缓存协议失败测试

**文件：** `tests/test_anthropic.py`

**依赖：** T7

**步骤：**

1. 捕获请求体，断言稳定 System 为带 `cache_control` 的文本块。
2. 断言最后一个稳定工具定义带缓存控制，其他工具顺序不变。
3. 断言环境和 Plan reminder 为不带缓存控制的后续系统块。
4. 断言用户、助手、工具调用和结果顺序保持原协议形态。
5. 模拟缓存创建、读取、缺失、零、布尔、负数和字符串字段。
6. 断言总输入为未缓存输入、创建和读取之和，非法明细保持未知。

**验证：** `.venv/bin/python -m pytest tests/test_anthropic.py -q -p no:cacheprovider` 只在新增缓存行为上失败。

## T9：实现 Anthropic 缓存映射

**文件：** `mewcode/providers/anthropic.py`、`tests/test_anthropic.py`

**依赖：** T8

**步骤：**

1. 把稳定系统项和动态系统补充项序列化为有序 system 内容块。
2. 只在稳定系统块添加 `cache_control: ephemeral`。
3. 复制本轮工具定义并只给最后一个工具添加缓存控制，不修改注册表原对象。
4. 编写只接受非负非布尔整数的用量读取辅助逻辑。
5. 解析创建、读取和总输入快照，保留既有正文、工具和错误处理。
6. 验证不同动态 reminder 下稳定块逐字节相同。

**验证：** `.venv/bin/python -m pytest tests/test_anthropic.py -q -p no:cacheprovider` 全部通过。

## T10：建立 OpenAI-compatible 缓存协议失败测试

**文件：** `tests/test_openai.py`

**依赖：** T7

**步骤：**

1. 断言稳定 System 位于第一条消息，动态 reminder 紧随且角色为 `system`。
2. 断言用户同名标签仍序列化为 `user`。
3. 断言请求不包含 `cache_control`、TTL 或自定义缓存参数。
4. 模拟 `prompt_tokens_details.cached_tokens` 与可选写入字段。
5. 模拟 DeepSeek `prompt_cache_hit_tokens` 兼容字段及标准字段优先级。
6. 覆盖缺失、零、布尔、负数和字符串字段，断言未知与零严格区分。

**验证：** `.venv/bin/python -m pytest tests/test_openai.py -q -p no:cacheprovider` 只在新增缓存行为上失败。

## T11：实现 OpenAI-compatible 缓存映射

**文件：** `mewcode/providers/openai.py`、`tests/test_openai.py`

**依赖：** T10

**步骤：**

1. 把 `SYSTEM_REMINDER` 序列化为 `role: system`，保持其相对顺序。
2. 保证稳定系统和稳定工具定义序列化结果不包含动态字段或缓存扩展。
3. 编写非负非布尔整数解析辅助逻辑。
4. 优先读取标准 `prompt_tokens_details.cached_tokens`，兼容 DeepSeek 命中字段。
5. 端点提供合法写入字段时映射为统一缓存写入值。
6. 保持现有思考、正文、工具分片和错误脱敏测试通过。

**验证：** `.venv/bin/python -m pytest tests/test_openai.py -q -p no:cacheprovider` 全部通过。

## T12：强化工具描述并验证双重规则

**文件：** `mewcode/tools/registry.py`、`tests/test_prompts.py`、相关工具测试

**依赖：** T3

**步骤：**

1. 更新 `find_files` 和 `search_code` 描述，声明优先于通用命令执行。
2. 更新 `read_file`、`edit_file` 和 `write_file` 描述，声明编辑前读取与精确修改。
3. 更新 `run_command` 描述，声明不得替代专用工具且只能依据实际结果报告。
4. 在测试中同时检索固定工具使用模块和相关工具描述中的四类规则。
5. 断言工具名称、参数 schema、顺序和 read-only 标记不变。

**验证：** `.venv/bin/python -m pytest tests/test_prompts.py tests/test_tool_registry.py -q -p no:cacheprovider` 全部通过。

## T13：建立 Agent 注入与用量失败测试

**文件：** `tests/test_agent.py`、`tests/test_app.py`

**依赖：** T5、T7、T9、T11

**步骤：**

1. 捕获正常模式请求，断言每轮有新的环境 reminder 且稳定 System 不变。
2. 模拟 17 轮 Plan Mode，断言完整提醒只在 1、6、11、16 轮。
3. 断言新 `run()` 和模式切换后从完整提醒重新开始。
4. 断言 Plan Mode 只暴露只读工具，执行层仍拒绝副作用调用。
5. 模拟同一轮多个用量快照和多轮请求，断言每轮最终快照只累计一次。
6. 添加 TUI 状态栏不出现 cache/read/write 文本的回归断言。

**验证：** `.venv/bin/python -m pytest tests/test_agent.py tests/test_app.py -q -p no:cacheprovider` 只在新增行为上失败。

## T14：接入 Agent Loop 动态上下文与缓存用量

**文件：** `mewcode/agent.py`、`tests/test_agent.py`、`tests/test_app.py`

**依赖：** T13

**步骤：**

1. 移除把平台和 Plan 文本拼入稳定 System Prompt 的逻辑。
2. 每轮异步构造环境 reminder，并在 Plan Mode 追加该轮模式 reminder。
3. 使用 Conversation 临时注入接口构造 Provider 历史。
4. 保留本轮最后一个 `TokenUsage` 快照，在现有提交边界只合并一次。
5. 扩展会话累计缓存字段，区分未知和明确零。
6. 保持取消、错误、迭代上限和工具结果配对路径不变。

**验证：** `.venv/bin/python -m pytest tests/test_agent.py tests/test_app.py -q -p no:cacheprovider` 全部通过。

## T15：完成跨协议与回归验证

**文件：** T3–T14 涉及的实现与测试文件

**依赖：** T9、T11、T12、T14

**步骤：**

1. 添加同一逻辑上下文在两个 Provider 中模块、环境、reminder 和历史顺序等价的断言。
2. 运行 Prompt、环境、Conversation、两个 Provider、Agent、工具和 TUI 聚焦测试。
3. 运行完整 Python 测试与编译检查。
4. 检查没有把动态内容混进稳定模块，没有把 reminder 写入历史。
5. 检查 TUI 没有新增缓存展示，Provider 公共签名没有改变。

**验证：** `.venv/bin/python -m pytest tests/test_prompts.py tests/test_environment.py tests/test_conversation.py tests/test_anthropic.py tests/test_openai.py tests/test_agent.py tests/test_tool_registry.py tests/test_app.py -q -p no:cacheprovider`、`.venv/bin/python -m pytest -q -ra -p no:cacheprovider` 和 `.venv/bin/python -m compileall -q mewcode tests scripts` 均退出 0。

## T16：实现缓存 smoke 脚本

**文件：** `scripts/smoke_prompt_cache.py`

**依赖：** T9、T11、T14

**步骤：**

1. 复用现有配置加载和 Provider 工厂，不复制请求协议逻辑。
2. 接收 Provider 名称与轮次数参数，默认发送两轮无副作用固定请求。
3. 记录请求到第一个公开正文增量的等待时间。
4. 每轮只打印 Provider、模型、输入、输出、缓存读取、缓存写入和首字等待；未知显示 `unknown`。
5. 确保脚本不打印配置原文、API Key、Authorization、环境变量或隐藏思考。
6. 添加 `--help` 和无配置/未知 Provider 的可读错误。

**验证：** `.venv/bin/python scripts/smoke_prompt_cache.py --help` 退出 0；使用测试替身或现有 Provider 测试验证输出脱敏且未知状态正确。

## T17：编写人工 A/B 评估文档

**文件：** `specs/06-system-prompt-cache/manual-evaluation.md`

**依赖：** T3、T12、T14、T16

**步骤：**

1. 固定五个 Spec 要求的场景和逐字输入。
2. 为每个场景列出工具调用顺序、违规、最终质量、缓存读写和首字等待记录栏。
3. 规定旧提示与新提示使用同一 Provider、模型、工具集、配置和工作区状态。
4. 规定只记录公开可观察行为，不记录隐藏思考、密钥或文件内容。
5. 增加外部缓存门槛、过期和尽力而为导致未命中的说明。

**验证：** 搜索文档确认五个场景和六类观察项齐全，不包含自动分数、不同模型比较或真实敏感数据。

## T18：执行 tmux 端到端验收并记录证据

**文件：** `specs/06-system-prompt-cache/checklist.md`、`specs/06-system-prompt-cache/manual-evaluation.md`、`specs/06-system-prompt-cache/acceptance-report.md`

**依赖：** T15、T16、T17

**步骤：**

1. 在 tmux 中启动 MewCode，不读取或打印用户 API Key。
2. 运行普通模式的“查找 → 读取 → 精确修改 → 验证”无敏感测试夹具场景。
3. 进入 Plan Mode，运行至少一轮只读任务并验证副作用工具不可用；切回 `/do` 验证执行工具恢复。
4. 用相同稳定前缀运行两轮以上，记录 Provider 实际缓存写入、读取或未知状态和首字等待。
5. 按人工 A/B 文档记录五个场景；若无法恢复旧运行版本，使用开发前已捕获的基线记录并明确证据边界，不伪造结果。
6. 逐项执行 checklist，把实际命令、通过数、失败数和外部阻塞写入验收报告。
7. 运行 `git diff --check`、`git status --short`，确认无密钥、临时夹具、缓存文件或无关改动进入变更集。

**验证：** tmux 中至少完成一次真实“用户输入 → 动态系统补充 → 工具调用 → 工具结果 → 最终答复”链路；全量测试无回归；验收报告如实区分通过、未通过和外部限制。

## 执行顺序

```text
T1 → T2 → T3 ─┬→ T4 → T5 ─┐
              ├→ T6 → T7 ─┼→ T8 → T9 ─┐
              │           └→ T10 → T11 ├→ T13 → T14 → T15 ─┐
              └→ T12 ──────────────────┘                    ├→ T16 → T17 → T18
                                                            ┘
```

开发期间不执行 commit、push 或 PR；除非用户另行明确授权。
