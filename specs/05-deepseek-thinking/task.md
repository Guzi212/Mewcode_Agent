# OpenAI 兼容思考模式 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `mewcode/config.py` | 保留 thinking 三态并验证布尔类型 |
| 修改 | `mewcode/messages.py` | 增加内部思考流事件和隐藏历史字段 |
| 修改 | `mewcode/conversation.py` | 原子保存正文、思考内容与工具调用 |
| 修改 | `mewcode/agent.py` | 累积、隔离并按条件保存思考增量 |
| 修改 | `mewcode/providers/base.py` | 更新 Provider 思考内容契约说明 |
| 修改 | `mewcode/providers/openai.py` | 通用扩展开关、SSE 解析与响应驱动历史回传 |
| 修改 | `mewcode.yaml.example` | 说明 OpenAI-compatible 的 `thinking` 三态行为 |
| 修改 | `tests/test_config.py` | thinking 缺失、真假与非法类型测试 |
| 修改 | `tests/test_openai.py` | 请求体、解析、序列化和协议隔离测试 |
| 修改 | `tests/test_agent.py` | 公开事件隔离、多轮工具和恢复测试 |
| 修改 | `tests/test_conversation.py` | 隐藏思考内容的历史关联测试 |

## T1：记录基线并建立失败回归测试

**文件：** `tests/test_config.py`、`tests/test_openai.py`、`tests/test_agent.py`、`tests/test_conversation.py`

**依赖：** 无

**步骤：**

1. 记录当前 Git 状态，只处理本规格文件，不覆盖现有未提交改动。
2. 运行四组相关测试，保存开发前基线。
3. 添加任意模型名下 enabled、disabled 和未配置三种请求体测试。
4. 添加包含 `reasoning_content` 与工具调用的 SSE 测试，要求产生内部思考事件但正文不含秘密字符串。
5. 添加 Agent 多轮测试，要求工具回合保存思考内容且公开 `AgentEvent` 不泄漏。
6. 添加历史序列化测试，要求第二次请求携带思考、工具调用和紧随其后的工具结果；无前置正文时助手 `content` 必须是空字符串。
7. 运行新增测试并确认它们因缺失功能失败，而不是夹具、导入或环境错误。

**验证：** 运行 `& '.\.venv\Scripts\python.exe' -m pytest tests/test_config.py tests/test_openai.py tests/test_agent.py tests/test_conversation.py -q -p no:cacheprovider`；新增断言稳定失败，既有测试保持原基线。

## T2：扩展内部事件与会话历史数据契约

**文件：** `mewcode/messages.py`、`mewcode/conversation.py`、`mewcode/providers/base.py`、`tests/test_conversation.py`

**依赖：** T1

**步骤：**

1. 为统一流增加只供内部消费的思考增量类型。
2. 为 `StreamEvent` 增加独立 `reasoning` 字段与构造入口，不复用正文 `text`。
3. 为 `ConversationItem` 增加默认空的 `reasoning_content`。
4. 扩展助手历史项与 Conversation 写入入口，保持旧调用参数兼容。
5. 更新契约注释，明确 Provider 不直接展示、Agent 不公开转发。
6. 让历史关联测试通过，确认正文、思考和工具调用位于同一助手项。

**验证：** 运行 `./.venv/Scripts/python.exe -m pytest tests/test_conversation.py -q -p no:cacheprovider`，期望全部通过。

## T3：实现通用 OpenAI-compatible 思考扩展

**文件：** `mewcode/config.py`、`mewcode/providers/openai.py`、`tests/test_config.py`、`tests/test_openai.py`

**依赖：** T2

**步骤：**

1. 把 `thinking` 配置改为可选布尔值，缺失保留为 `None`，非法类型报配置错误。
2. OpenAI Provider 仅在值非 `None` 时发送 enabled/disabled，不判断模型名或地址。
3. 任何 OpenAI 兼容响应的非空 `reasoning_content` 都转为内部思考事件。
4. 序列化非空 `reasoning_content`，并仅为这类无前置正文的工具调用助手把 `content` 写为空字符串。
5. 保持正文、工具分片、用量和错误处理逻辑不变。
6. 更新原有“直接丢弃思考”的测试，使其验证“内部可用、正文不可见”。

**验证：** 运行 `& '.\.venv\Scripts\python.exe' -m pytest tests/test_config.py tests/test_openai.py -q -p no:cacheprovider`，期望配置三态、请求体、SSE、工具序列化和未配置隔离测试全部通过。

## T4：接入 Agent Loop 并保证不公开思考内容

**文件：** `mewcode/agent.py`、`tests/test_agent.py`

**依赖：** T2、T3

**步骤：**

1. 每次 Agent 迭代创建独立思考增量缓冲。
2. 收到内部思考事件时只追加缓冲，不发布任何 `AgentEvent`。
3. 本轮存在工具调用时，把完整思考内容传给助手历史写入入口。
4. 本轮无工具调用、未形成完整调用即取消或流错误时，不保存该轮思考内容。
5. 保持工具执行期间取消时的结构化结果补全逻辑。
6. 覆盖连续两轮工具调用，验证每轮思考内容归属和最终正常停止。
7. 遍历公开事件及错误、工具摘要，断言均不含测试秘密字符串。

**验证：** 运行 `./.venv/Scripts/python.exe -m pytest tests/test_agent.py -q -p no:cacheprovider`，期望多轮、取消、错误和思考隔离测试全部通过。

## T5：更新配置示例并完成聚焦回归

**文件：** `mewcode.yaml.example` 及 T1–T4 涉及文件

**依赖：** T3、T4

**步骤：**

1. 在示例中说明 OpenAI-compatible 的 `thinking` 是显式扩展：true/false 会发送，缺失不发送。
2. 不添加真实密钥，不读取或修改用户的 `mewcode.yaml`。
3. 检查所有新增内容无调试日志、思考内容输出和无关重构。
4. 运行三组聚焦测试和 Python 编译检查。

**验证：** 运行：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest tests/test_config.py tests/test_openai.py tests/test_agent.py tests/test_conversation.py -q -p no:cacheprovider
& '.\.venv\Scripts\python.exe' -m compileall -q mewcode tests
```

两条命令均应退出 0。

## T6：运行全量自动化回归

**文件：** 不新增功能文件；仅修复本规格引入的回归

**依赖：** T5

**步骤：**

1. 运行完整 Python 测试且禁用不可写 pytest 缓存。
2. 若出现失败，区分本次回归、已有平台限制与需要显式启用的原生测试。
3. 只修复与本规格直接相关的失败，不修改沙箱或跨平台功能以追求绿色结果。
4. 运行 `git diff --check` 并核对变更范围。

**验证：** 运行 `./.venv/Scripts/python.exe -m pytest -q -ra -p no:cacheprovider`、`git diff --check`；本规格不得新增失败。

## T7：执行真实 DeepSeek tmux 验收

**文件：** 用户本地忽略 Git 的 `mewcode.yaml` 由用户确认配置；实现文件不再修改，除非验收暴露真实缺陷

**依赖：** T6

**步骤：**

1. 确认用户配置中 Pro 与 Flash 均为 `thinking: true`，但不读取、打印或复制 API Key。
2. 检查原生 tmux；Windows 无 tmux 时检查 WSL 中是否已有 tmux 和可运行的 MewCode 环境。
3. 在 tmux 启动 MewCode，分别选择 Pro 与 Flash。
4. 输入一个必须读取项目文件后总结的真实请求，确保模型实际产生工具调用和至少一次结果回灌。
5. 观察工具完成后模型继续输出最终答复，界面没有 HTTP 400、思考文本或未捕获堆栈。
6. 按 checklist 逐项记录脱敏结果；若 tmux、网络或配置前置缺失，明确记录阻塞项并等待用户处理，不以 mock 结果替代。

**验证：** Pro 与 Flash 各完成一次“用户请求 → 思考 → 工具调用 → 工具结果 → 最终答复”，正常返回输入状态并可继续对话。

## 执行顺序

```text
T1 → T2 → T3 → T4 → T5 → T6 → T7
```

开发期间不执行 commit、push 或 PR；除非用户另行明确授权。
