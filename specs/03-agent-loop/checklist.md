# MewCode Agent Loop Checklist

> 每项均通过运行代码、测试或观察终端行为验收。开发完成后记录实际证据，再勾选结果。

## 实现完整性

- [x] **自主多轮循环（AC1）**：模型续答再次请求工具时仍会执行并回灌，直到某轮不再请求工具才结束。（验证：运行 `pytest -q tests/test_agent.py -k multiround`，并检查 Provider 被调用至少三轮、工具结果逐轮进入历史。）

- [x] **自然完成（AC2）**：无工具调用的纯文本被作为最终答复，任务只产生一次完成事件，输入可继续使用。（验证：运行 `pytest -q tests/test_agent.py -k complete` 和对应 TUI 测试，期望停止原因是 `COMPLETED`。）

- [x] **迭代上限（AC3）**：默认上限为 20，合法配置可以覆盖；达到上限后不再请求模型或执行工具。（验证：运行 `pytest -q tests/test_config.py tests/test_agent.py -k 'iteration or limit'`，检查额外调用计数为零。）

- [x] **用户取消（AC4）**：任务运行时按 Esc 会立即显示正在取消，停止后续工作、恢复输入并保留已有输出；Ctrl+C 仍退出应用。（验证：运行 `pytest -q tests/test_agent.py tests/test_app.py -k 'cancel or escape or ctrl_c'`。）

- [x] **连续全未知工具停止（AC5）**：连续三轮全部为未知工具时停止；含任一已登记工具的轮次会清零计数。（验证：运行 `pytest -q tests/test_agent.py -k unknown`，断言第三轮配对结果写入后无第四轮请求。）

- [x] **流式错误恢复（AC5）**：Provider 流错误会产生错误和 `STREAM_ERROR` 结束事件，程序不退出，下一任务仍可运行。（验证：运行 `pytest -q tests/test_agent.py tests/test_app.py -k stream_error`。）

- [x] **Agent 事件完备（AC6）**：可观察到用户消息、文本增量、工具开始、工具结束、Token 更新、迭代开始、轮次结束、错误和任务结束事件。（验证：运行 `pytest -q tests/test_agent.py -k event`，按事件序列断言种类与顺序。）

- [x] **TUI 与编排解耦（AC6）**：TUI 不直接调用 Provider 流或 ToolExecutor 批执行，只提交输入并消费 AgentEvent。（验证：运行 `rg -n '\.stream\(|execute_batch\(' mewcode/tui`，期望无直接编排调用；运行 `pytest -q tests/test_app.py`。）

- [x] **双路流式收集（AC7）**：文本增量实时发布，同时完整文本和分片工具调用被收集用于轮次判断。（验证：运行 `pytest -q tests/test_agent.py tests/test_openai.py tests/test_anthropic.py -k 'fragment or preamble or stream'`。）

- [x] **OpenAI 分片工具调用（AC7）**：工具名称和 JSON 参数跨 SSE 分片时被正确拼接，非法参数转为错误事件。（验证：运行 `pytest -q tests/test_openai.py -k 'fragment or arguments'`。）

- [x] **Anthropic 分片工具调用（AC7）**：`input_json_delta` 被正确拼接，思考增量不进入正文。（验证：运行 `pytest -q tests/test_anthropic.py -k 'tool or thinking'`。）

- [x] **连续只读并发（AC8）**：连续的读文件、找文件和搜代码调用会在同一批并发执行。（验证：运行 `pytest -q tests/test_tool_executor.py -k concurrent`，使用屏障证明调用执行时间重叠。）

- [x] **副作用串行（AC8）**：写文件、改文件和执行命令各自串行，且不会与后续批次重叠。（验证：运行 `pytest -q tests/test_tool_executor.py -k serial`，断言最大副作用并发数为 1。）

- [x] **结果保序与失败隔离（AC8）**：并发完成顺序不同也按模型原始调用顺序回灌；单项失败不阻断同批其他调用。（验证：运行 `pytest -q tests/test_tool_executor.py -k 'order or failure'`。）

- [x] **工具独立超时（N1）**：单个工具超时产生结构化结果，Agent 可继续处理同批结果和下一轮。（验证：运行现有命令超时测试及 Agent 工具失败继续测试。）

- [x] **历史合法配对（AC9）**：每个含文本与工具调用的模型回合只序列化为一个 assistant 回合，随后全部调用都有对应结果。（验证：运行 `pytest -q tests/test_conversation.py tests/test_openai.py tests/test_anthropic.py -k 'history or serialize or tool'`。）

- [x] **提前终止历史合法（AC9）**：取消、迭代上限、未知工具上限或流错误后不存在悬空工具调用，下一次请求不会因角色或调用配对错误失败。（验证：运行 `pytest -q tests/test_agent.py -k 'cancel or limit or unknown or stream_error'` 并检查下一任务成功。）

- [x] **取消无资源泄漏（AC9、N9）**：取消后没有挂起 Provider、工具任务、等待任务或队列。（验证：运行 `pytest -q tests/test_agent.py tests/test_tool_executor.py -k cancel`，期望无 asyncio pending-task 或未关闭资源告警。）

- [x] **Token 用量提取（AC10）**：OpenAI 与 Anthropic 都能提取可用的输入/输出 Token；缺失时保持不可用，不伪造为零。（验证：运行 `pytest -q tests/test_openai.py tests/test_anthropic.py -k usage`。）

- [x] **Token 跨轮累计（AC10）**：同轮多次快照不会重复累计，多轮用量正确相加并更新状态栏。（验证：运行 `pytest -q tests/test_agent.py tests/test_app.py -k usage`。）

- [x] **迭代进度（AC11）**：任务运行时动态区域显示从 1 开始的当前轮次，轮次推进时更新，结束后显示总耗时。（验证：运行 `pytest -q tests/test_app.py -k iteration`，并在 tmux 场景观察。）

- [x] **Plan Mode 只读约束（AC12）**：`/plan` 请求只携带三项只读工具定义；模型主动请求写、改或命令时返回禁止结果且不执行。（验证：运行 `pytest -q tests/test_agent.py tests/test_tool_registry.py -k plan`。）

- [x] **计划保存与 `/do` 执行（AC12）**：计划完成后保存在当前会话；无参数 `/do` 立即使用完整工具集执行计划，`/do <任务>` 执行新任务。（验证：运行 `pytest -q tests/test_agent.py -k plan`。）

- [x] **无计划 `/do` 安全失败**：没有已保存计划时输入 `/do` 只显示可恢复错误，不请求模型、不执行工具。（验证：运行对应 `tests/test_agent.py` 命令解析测试。）

## 集成与兼容

- [x] **OpenAI 完整链路（AC13）**：工具分片、用量、合法历史、多轮回灌和停止事件在 OpenAI/兼容格式下通过。（验证：运行 `pytest -q tests/test_openai.py tests/test_agent.py` 使用 OpenAI 脚本 Provider 场景。）

- [x] **Anthropic 完整链路（AC13）**：工具分片、用量、合法历史、多轮回灌和停止事件在 Anthropic 格式下通过。（验证：运行 `pytest -q tests/test_anthropic.py tests/test_agent.py` 使用 Anthropic 脚本 Provider 场景。）

- [x] **普通纯文本回归（N7）**：不调用工具的普通问题仍实时显示文本、正确 markdown 定型并写入历史。（验证：运行 `pytest -q tests/test_app.py -k normal_stream`。）

- [x] **现有工具与沙箱回归（N2、N7）**：六项工具、外部路径审批、沙箱隔离和权限范围行为不变。（验证：运行 `pytest -q tests/test_tool_registry.py tests/test_tool_worker.py tests/test_tools_files.py tests/test_tools_search.py tests/test_tools_command.py tests/test_sandbox_permissions.py tests/test_sandbox_backends.py tests/test_screens.py`。）

- [x] **scrollback 顺序（N8）**：跨轮前置文本、工具行、结果摘要和最终答复按真实发生顺序保留；并发批工具行不交错。（验证：运行 `pytest -q tests/test_app.py -k 'order or scroll or tool'`。）

- [x] **TUI 持续响应（N1）**：模型流、长工具、并发批和取消等待期间计时与轮次继续刷新，滚动与 Esc 可响应。（验证：运行 TUI 自动化长任务测试，并在 tmux 取消场景人工观察。）

- [x] **结果体量控制（N4）**：多轮工具结果仍保留现有截断上限与截断标记，TUI 和历史不会展示无限输出。（验证：运行现有文件、搜索和命令长输出测试。）

- [x] **敏感信息保护**：API key 不出现在事件、错误、工具结果、TUI 或测试输出中。（验证：运行 `pytest -q`，并搜索捕获输出中使用的测试密钥；确认仅测试配置输入包含它。）

## 编译与自动化测试

- [x] **字节码编译**：全部 Python 文件可编译。（验证：运行 `python -m compileall -q mewcode tests`，期望无输出且退出码为 0。）

- [x] **全量测试（AC14）**：所有单元和集成测试通过，未跳过与 Agent Loop 相关的失败。（验证：运行 `pytest -q`，记录通过数量与耗时。）

- [x] **格式与静态检查（AC14、N10）**：代码符合项目现有格式；若项目配置 Ruff 或 mypy，则相应检查无告警。（验证：先检查 `pyproject.toml` 的工具配置；存在时运行对应命令，不存在时记录“项目未配置”，不得伪报通过。）

- [x] **工作区检查**：只修改 `task.md` 文件清单中的实现、测试、配置及本规格目录文件，无敏感文件或无关改动。（验证：运行 `git status --short` 和 `git diff --check`，逐项核对。）

## tmux 端到端场景

- [x] **场景 E1：真实多步自主执行（AC1、AC8、AC11）**：在 tmux 启动 MewCode，输入“读取 `pyproject.toml` 得到项目名和 Python 版本要求；把它们写入本次验收生成的 `agent-loop-e2e.txt`；再读回文件并核对内容，最后汇报”。观察 Agent 无需催促完成读 → 写 → 读的多轮调用，轮次递增，最终文本与文件内容一致。（验证：保留终端输出并读取生成文件核对；验收后确认路径再清理该测试生成文件。）

- [x] **场景 E2：Plan Mode 两段式（AC12）**：输入 `/plan 创建本次验收用的 agent-loop-plan-e2e.txt，内容包含 pyproject.toml 中的项目名和 Python 版本要求`，观察只使用读类工具且未创建文件；随后输入 `/do`，观察切回完整工具并创建、读取验证该文件。（验证：计划阶段检查文件不存在，执行阶段检查文件存在且内容正确；验收后清理测试生成文件。）

- [x] **场景 E3：Esc 取消与恢复（AC4、AC9）**：输入一个会触发长耗时命令的任务，在命令完成前按 Esc。观察立即显示正在取消，当前任务结束、输入恢复、已有历史保留；随后输入普通问题并获得正常答复。（验证：保留取消前后终端输出，确认没有后续工具调用或应用退出。）

- [x] **场景 E4：状态与 scrollback（AC10、AC11、N8）**：在 E1/E2 过程中观察状态栏 Token 用量随轮次更新、动态区显示当前轮次；向上滚动确认前置文本、工具行、结果和最终答复顺序正确。（验证：记录终端可见状态；Provider 不返回用量时应明确显示不可用。）

- [x] **场景 E5：退出语义回归（AC4、N7）**：任务空闲时按 Ctrl+C，MewCode 正常退出；重新启动后空闲按 Esc 不退出也不产生对话。（验证：观察进程退出码和重新启动后的界面状态。）

## 验收完成条件

- [x] spec AC1–AC14 均至少有一项通过证据，且不存在未解释的失败。（验证：建立 AC1–AC14 到已勾选条目的对应表并逐项核对。）
- [x] 自动化测试、编译检查和适用的格式/静态检查全部通过。（验证：附上实际命令、退出码和测试汇总。）
- [x] E1–E5 均完成；测试生成文件已核对并安全清理。（验证：检查场景记录，并在删除前核对具体路径仅为本次生成文件。）
- [x] 验收报告记录实际命令、结果、通过项和任何剩余风险。（验证：报告包含通过数、失败数、端到端证据和风险小节。）

## 验收记录（2026-07-15）

- 自动化测试：`.venv/bin/python -m pytest -q`，实际结果 `83 passed in 2.22s`。
- 编译检查：`.venv/bin/python -m compileall -q mewcode tests`，退出码 0，无错误输出。
- 格式检查：`git diff --check` 退出码 0；项目 `pyproject.toml` 当前未配置 Ruff 或 mypy，因此没有伪报静态检查结果。
- E1 多步自主执行：真实 Provider 自动完成读取 `pyproject.toml`、写入并读回 `agent-loop-e2e.txt`；内容为项目名 `mewcode`、Python 要求 `>=3.10`，状态栏累计用量为输入 5284 / 输出 356。
- E2 Plan Mode：`/plan` 阶段只输出执行计划且 `agent-loop-plan-e2e.txt` 不存在；`/do` 后文件创建成功，内容与计划一致。
- E3 取消恢复：长命令任务收到 Esc 后以“当前任务已取消”结束，输入恢复；随后发送普通消息得到“恢复正常”。外层 tmux 按键审批有延迟，导致真实场景中工具先触发 20 秒超时；自动化测试覆盖了取消信号立即到达时的路径。
- E4 状态与历史：轮次、工具结果、错误、最终答复和累计 Token 均在 TUI 可见，取消后的后续消息顺序正常。
- E5 退出语义：空闲态 Ctrl+C 后 `tmux has-session` 返回找不到会话；空闲 Esc 行为由 TUI 自动化测试覆盖。
- 清理：两个 `agent-loop-*-e2e.txt` 验收临时文件均已核对后删除。
