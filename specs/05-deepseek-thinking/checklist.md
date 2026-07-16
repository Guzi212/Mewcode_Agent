# OpenAI 兼容思考模式 Checklist

> 每项必须用测试、捕获的脱敏请求或真实 tmux 行为验证。思考内容使用固定测试秘密字符串验证隔离，禁止把真实模型思考内容保存到验收记录。

## 2026-07-16 验收记录

- 自动化：配置、OpenAI、Anthropic、Agent、Conversation 与 Windows helper 协议回归全部通过；`compileall` 退出码 0；完整 Python 测试目录为 **161 passed、11 skipped、0 failed**。Rust helper 测试无失败，`cargo fmt --check` 通过；11 项 Python skip 与 Rust ignored 项均为既有平台权限或需显式启用的原生测试。
- 全链路 mock：非 DeepSeek 模型名通过同一个 OpenAI Provider 完成“思考增量 → 工具调用 → 工具结果 → 第二次请求 → 最终正文”，捕获的第二次请求包含 `reasoning_content`、非 null `content` 和紧随其后的工具结果；公开 Agent 事件不含测试思考秘密。
- 兼容性：`thinking: true`、`false`、缺失分别验证为 enabled、disabled、不发送；响应解析和回传不检查模型名、厂商或 `base_url`。官方 OpenAI 未启用扩展时请求保持原形，Anthropic 回归通过。
- 隐私：HTTP 错误中的 `reasoning_content`、`api_key`、Authorization 类字段会在进入公开错误事件前脱敏；diff 未包含真实配置、密钥、调试日志或思考持久化。
- 根目录裸 `pytest` 会递归进入 Rust `target/pytest-*` AppContainer 夹具并因其 ACL 产生 9 个收集错误；显式完整 Python 范围 `pytest tests` 已全部执行，不删除目录、不改 ACL、不跳过测试。
- 真实 E2E 环境：已在正常 Windows 用户会话确认 Ubuntu（WSL 2）已注册，WSL 内 `/usr/bin/tmux` 为 3.4、Python 为 3.12.3，项目可从 `/mnt/d/Claude code/works/MewCode_Agent` 访问；此前“没有发行版/tmux”和 `setup_required` 均是应用沙箱未读取到用户级状态造成的误判。正常用户会话中的 `mewcode sandbox diagnose` 与 TUI 均为 `windows-appcontainer/ready`。
- 真实 API：Pro 与 Flash 均在 tmux 中启用思考模式，成功调用 `read_file` 读取仅含公开固定值的临时夹具，工具结果回灌后返回正确值；无 HTTP 400、堆栈或可见思考内容。为避免把仓库内容发送给第三方服务，真实验收未读取 `README.md`。
- 连续工具：Flash 按 `find_files → read_file → 最终正文` 完成两轮工具回灌，界面只显示工具状态与最终答案。首次 Pro 工具执行暴露 editable install 与 helper 实体包目录假设不兼容；协议 v2 现显式传递实际包目录，并避免源码包与 workspace 的重叠 ACL，修复后本地沙箱冒烟及真实模型调用均通过。
- 恢复：修复前同一 Pro 会话出现一次脱敏的 `acl_apply_failed`；更新 helper 后下一条请求成功，应用未退出。Provider HTTP 400 后恢复仍由自动化回归覆盖。

## 实现完整性

- [x] **显式启用（AC1）**：任意 OpenAI-compatible 模型配置 `thinking: true` 时，请求体包含 `thinking.type=enabled`，无需模型名单。（验证：使用非 DeepSeek 测试模型名捕获 JSON。）
- [x] **显式关闭（AC1）**：显式配置 `thinking: false` 时请求体包含 `thinking.type=disabled`。（验证：参数化请求捕获测试。）
- [x] **未配置隔离（AC1、AC6）**：缺失 `thinking` 时请求中完全不存在该字段，严格 OpenAI 端点不受影响。（验证：现有 OpenAI 请求测试增加否定断言。）
- [x] **配置类型（AC1）**：true、false、缺失分别加载为 `True`、`False`、`None`；字符串等非法值启动时报可读错误。（验证：配置测试。）
- [x] **内部流事件（AC2）**：任意模型名的 OpenAI-compatible SSE 返回多个 `reasoning_content` 分片时，按顺序进入内部思考字段，正文 `text` 不包含测试秘密。（验证：Provider SSE 测试。）
- [x] **最终回合丢弃（AC2、N1）**：无工具调用的最终文本回合不把思考内容写入 Conversation。（验证：Agent 纯文本思考测试。）
- [x] **工具回合保存（AC3）**：有工具调用时，前置正文、完整思考内容和工具调用位于同一助手历史项。（验证：Conversation 与 Agent 测试。）
- [x] **后续请求回传（AC3）**：工具结果后的下一次请求在对应助手消息中包含 `reasoning_content`，工具结果紧随其后。（验证：捕获第二次请求 JSON。）
- [x] **助手正文非 null（AC3）**：带 `reasoning_content` 的兼容响应只返回工具调用、没有前置正文时，后续请求中的助手 `content` 为 `""`；无思考历史的普通 OpenAI 路径保持既有行为。（验证：参数化历史序列化测试。）
- [x] **连续工具轮次（AC4）**：至少两轮工具调用的思考内容分别归属各自助手消息，不交换、不合并，最终任务正常完成。（验证：Agent 多轮测试。）

## 隐私与公开事件

- [x] **TUI 不可见（AC2、AC7）**：公开 `AgentEvent` 只含正文、工具和状态信息，不存在 reasoning 事件类型。（验证：遍历一次完整 Agent 事件流。）
- [x] **正文不泄漏（AC7）**：测试秘密字符串不出现在任何 `TEXT_DELTA`、最终助手正文或 Conversation 的公开 `messages` 列表中。（验证：Agent 与 Conversation 断言。）
- [x] **工具与错误不泄漏（AC7、AC8）**：测试秘密字符串不出现在工具摘要、工具错误、HTTP 错误或任务停止文本中。（验证：工具失败及 HTTP 400 模拟测试。）
- [x] **不持久化（N1）**：实现没有新增思考日志、文件写入、数据库字段或会话持久化路径。（验证：检查本规格 diff，并搜索新增 `reasoning` 使用点。）
- [x] **密钥安全（AC8）**：请求失败信息不包含 Authorization 头或 API Key。（验证：Provider 错误测试使用哨兵密钥并遍历事件文本。）

## 生命周期与恢复

- [x] **工具取消配对（AC5）**：工具执行期间取消后，助手工具调用和隐藏思考内容仍保留，随后存在匹配的取消结果。（验证：Agent 取消测试。）
- [x] **流中取消不留孤项（AC5）**：完整工具调用形成前取消，不保存孤立思考内容或未配对工具调用。（验证：阻塞 Provider 取消测试。）
- [x] **流错误恢复（AC8）**：DeepSeek HTTP 400 或 SSE 错误使当前任务以流错误停止，下一条任务仍可完成。（验证：两次连续 Agent 运行测试。）
- [x] **迭代上限保持合法（AC4、AC5）**：达到上限时，每个已执行工具调用都有结果，相关思考内容保持所属轮次。（验证：低迭代上限测试。）
- [x] **非思考模式工具可用（AC1、N2）**：`thinking: false` 下既有多轮工具调用仍正常完成。（验证：请求体测试 + Agent 既有多轮回归。）

## 协议兼容

- [x] **OpenAI 回归（AC6）**：普通 OpenAI 文本、工具分片、用量和错误测试全部通过，历史中没有新增空字段。（验证：`tests/test_openai.py`。）
- [x] **Anthropic 回归（AC6）**：Anthropic 请求和流式思考隐藏行为不变，不发送 DeepSeek 字段。（验证：`tests/test_anthropic.py`。）
- [x] **配置兼容（AC1、N2）**：既有 `thinking` 布尔配置继续加载；未配置时为 `None`，Anthropic 仍按关闭处理，OpenAI 不发送扩展。（验证：`tests/test_config.py` 与 Provider 测试。）
- [x] **旧构造兼容（N2）**：未传思考字段的 `ConversationItem`、`StreamEvent` 和 Conversation 调用方无需修改即可运行。（验证：现有 Conversation、Agent、Provider 全量测试。）

## 编译与自动化测试

- [x] **聚焦测试通过**：配置、OpenAI、Agent 和 Conversation 测试全部通过。（验证：`& '.\.venv\Scripts\python.exe' -m pytest tests/test_config.py tests/test_openai.py tests/test_agent.py tests/test_conversation.py -q -p no:cacheprovider`。）
- [x] **Provider 回归通过**：OpenAI 与 Anthropic 测试全部通过。（验证：`& '.\.venv\Scripts\python.exe' -m pytest tests/test_openai.py tests/test_anthropic.py -q -p no:cacheprovider`。）
- [x] **Python 编译通过**：实现与测试均无语法错误。（验证：`& '.\.venv\Scripts\python.exe' -m compileall -q mewcode tests`。）
- [x] **全量测试无新增失败**：平台适用测试全部通过，显式原生测试的跳过原因保持可解释。（验证：`& '.\.venv\Scripts\python.exe' -m pytest tests -q -ra -p no:cacheprovider`。）
- [x] **Diff 质量**：没有空白错误、调试代码、真实密钥、用户配置或无关改动。（验证：`git diff --check` 与 `git status --short`，逐项核对本规格文件。）

## 端到端场景

- [x] **E1：Pro 思考工具链（AC9）**：在 tmux 中选择 `deepseek-v4-pro` 且 `thinking: true`，真实 `read_file` 完成后模型继续返回正确固定值；无 HTTP 400、思考文本或堆栈。（验证：使用本次生成的无敏感临时夹具代替 `README.md`，避免向第三方披露仓库内容。）
- [x] **E2：Flash 思考工具链（AC9）**：在 tmux 中选择 `deepseek-v4-flash` 且 `thinking: true`，真实 `read_file` 完成并在工具结果回灌后返回正确固定值。（验证：脱敏记录与 E1 相同。）
- [x] **E3：连续工具调用（AC4、AC9）**：Flash 先调用 `find_files`，收到结果后再调用 `read_file`，最后返回正确固定值；界面未显示思考内容。（验证：工具顺序、三阶段完成状态与最终摘要，不记录思考内容。）
- [x] **E4：会话恢复（AC8）**：同一 Pro 会话先出现脱敏的 `acl_apply_failed`，helper 修复后下一条真实请求成功，应用未退出；Provider HTTP 400 恢复由自动化测试覆盖。（验证：错误代码和后续完成状态。）
- [x] **E5：tmux 环境真实性**：Ubuntu（WSL 2）内 `/usr/bin/tmux` 3.4 与 Python 3.12.3 可用，项目路径挂载检查退出码为 0，Windows 虚拟环境中的 MewCode 也可从 WSL 启动。（验证：正常用户会话只读检查；后续真实对话使用脱敏会话名 `mewcode-e2e`。）

## 验收完成条件

- [x] spec AC1–AC9 均至少有一条通过证据。
- [x] 聚焦测试、Provider 回归、Python 编译和全量测试均无本规格引入的失败。
- [x] Pro 与 Flash 的真实思考工具调用场景均通过。
- [x] 所有可见输出和验收记录均不包含真实思考内容、API Key 或 Authorization 信息。
- [x] 验收报告区分自动化通过项、真实 API 通过项和任何环境阻塞项。
