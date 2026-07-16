# MewCode 跨平台执行与 Windows 原生支持 Checklist

> 每项必须通过运行代码、测试或观察真实终端行为验证。开发完成后记录实际命令、退出码和脱敏证据，再勾选结果；不能用 mock 通过替代 Windows AppContainer 安全验收。

## 2026-07-16 Windows 原生验收记录

- Python：`python -m compileall -q mewcode tests scripts` 退出码 0；默认 `pytest -q -ra -p no:cacheprovider` 为 **141 passed、10 skipped、0 failed**。其中 8 项是必须显式启用的 Windows 原生测试，随后以 `--run-windows-native` 全部执行并得到 **8 passed、0 failed**；其余 skip 是只能在 macOS 验证的 Seatbelt 系统路径和当前会话缺少 symlink 创建权限，NTFS junction 原生逃逸测试已单独通过。
- Rust：`cargo fmt --check`、`cargo clippy --all-targets -- -D warnings`、`cargo test` 均退出码 0；`cargo test -- --ignored --test-threads=1` 的 **14 个真实 AppContainer/ACL 测试全部通过**。
- 真实 Helper：依次通过 diagnose、读、写、原子编辑、查找、搜索、PowerShell 环境、外部命令、非零退出、网络阻断、超时、工作区外读写阻断；三层进程树超时、Agent 取消、Python 宿主强退后均完成 Job/ACL 清理并可立即执行下一条命令。
- 真实 TUI 自动化：Textual 事件循环连接实际 WindowsSandbox/AppContainer，完成六类工具、非零退出、超时、一次授权/拒绝、Escape 取消、Ctrl+C 与后续对话恢复，共 **3 passed**；测试使用脚本 Provider，不读取真实 Provider 凭据。
- 安全边界：Helper 在恢复 suspended worker 前验证实际 token 为预期 AppContainer SID；capability 列表为空；本地 TCP 监听端未收到连接；临时 ACL 在覆盖、移动、新建后代、并发和崩溃恢复场景均精确回滚。
- 构建：locked release 和 `mewcode-0.1.0-py3-none-win_amd64.whl` 成功；wheel 含 Helper/manifest，PE 目标、组件版本和 SHA-256 匹配；pip 构建临时目录固定在 D 盘工作树 `target` 下。
- 安装包：D 盘隔离虚拟环境安装 wheel 后，`mewcode sandbox diagnose` 返回 `windows-appcontainer / ready / 0.1.0`；全新 D 盘源码副本又完成一次离线 Helper/wheel 构建、新 venv 安装、setup、diagnose 和完整边界 smoke，输出 `CLEAN_ROOM_READY`。
- 尚未完成：macOS 实机 Seatbelt/tmux 回归；连接用户真实模型 Provider 的 Windows Terminal/ConPTY 人工会话；全新 Windows 用户的首次 setup 观察。只需人工执行的步骤集中在 `docs/manual-acceptance.md`。

## 功能与行为

- [ ] **统一产品与自动后端选择（AC1）**：同一份 MewCode 配置在 macOS 与 Windows 启动相同 TUI、工具集和审批选项，并自动显示 Seatbelt 或 AppContainer 后端；不需要平台专用配置或功能分支。（验证：分别启动同一版本，运行后端诊断并对比可见工具定义与配置加载结果。）

- [x] **Windows 全工具原生隔离（AC2）**：读、写、改、找文件、搜索和命令工具全部通过 AppContainer Helper 执行；Helper 不可用时返回结构化失败，宿主机没有发生工具操作。（验证：真实 Helper smoke 覆盖六类工具；Python 后端测试覆盖组件不可用失败关闭。）

- [x] **工作区边界（AC2）**：工作区内读写成功；未授权工作区外读取和创建均失败且目标保持不变。（验证：真实 `outside_read`/`outside` smoke 返回 `file_read_error`/`file_write_error`，外部测试文件哈希不变且新目标不存在。）

- [x] **首次准备与幂等性（AC3）**：未准备环境显示 `SETUP_REQUIRED`；普通用户显式运行 setup 后变为 `READY`，重复运行不创建重复 AppContainer profile、不累积永久 ACL 或 capability。（验证：CLI 状态测试、真实 `setup_profile_is_idempotent` 与安装 wheel diagnose 通过；profile SID 保持一致。）

- [x] **支持基线诊断（AC3）**：Windows 10 1809+/11 x86-64 NTFS 环境可准备；错误架构、非 NTFS、组件缺失、版本过旧和企业策略阻止 profile 创建均显示可区分恢复提示。（验证：组件/路径/诊断参数测试通过，当前 Windows x86-64 NTFS 原生 diagnose 为 READY。）

- [ ] **平台原生命令语义（AC4）**：Windows 使用 PowerShell 变量、连接和错误流，macOS 使用 POSIX sh；stdout、stderr、退出码和中文 UTF-8 结果契约一致。（验证：运行 `pytest -q tests/test_shell.py tests/test_tools_command.py`，并在两平台执行对应原生命令 smoke。）

- [x] **模型知道当前平台（AC4）**：普通模式与 Plan Mode 的 system prompt 均只包含一次当前平台/Shell 上下文，不包含用户路径或敏感环境。（验证：平台/Shell/Plan prompt 自动化测试包含在全量 Python 回归中并通过。）

- [x] **超时回收完整进程树（AC5）**：命令创建父、子、孙进程后触发超时，全部进程在限定时间退出，结果为结构化超时，下一条命令可立即成功。（验证：真实 AppContainer E2E 创建 PowerShell 子进程和 Python 孙进程；超时后两个 PID 消失，后续命令成功。）

- [x] **取消与宿主退出清理（AC5）**：Agent 取消或 Python 宿主退出后，Helper 关闭 Job、终止全部后代并回滚 ACL；应用不挂起。（验证：真实 E2E 分别取消 WindowsSandbox task 和强制退出宿主 Python，Helper/worker 均退出、事务日志为 0、diagnose READY、下一次命令成功。）

- [x] **Windows 路径规范化（AC6）**：盘符、反斜杠、空格和大小写不同的同一合法路径正确处理；授权判断使用最终真实目标。（验证：Rust `path_policy` 的绝对路径、空格、大小写、文件 ID 与目标替换测试通过。）

- [x] **重解析点与最小授权（AC6）**：junction、symlink、挂载点和审批后替换不能逃逸；单文件授权不开放同目录其他文件，只读授权不能写。（验证：Rust 原生 reparse/目标替换测试通过；真实 AppContainer 单文件只读授权只允许目标读取，同目录兄弟读取和目标写入均失败且文件不变。）

- [x] **工具默认断网（AC7）**：AppContainer worker 及其子进程无法连接本地测试监听端口或外部地址，主应用 mock Provider 请求仍成功。（验证：真实 worker 连接宿主本地 TCP 监听端超时并返回 `network blocked`，监听端无连接；Provider mock 回归通过。）

- [x] **敏感环境清洗（AC7）**：工具环境、结果、stderr、诊断与测试日志中不存在注入的 API key、token 和 proxy 凭据。（验证：Shell 最小环境与 Rust 环境块测试拒绝 key/token/proxy；真实 worker 仅观测到非敏感 `MEWCODE_SANDBOX` 标记。）

- [ ] **跨平台审批语义（AC8）**：Windows 与 macOS 都显示规范化目标、工具、权限和命令摘要；拒绝、仅本次、当前会话行为一致，重启后会话授权失效。（验证：运行 `pytest -q tests/test_sandbox_permissions.py tests/test_screens.py`，并在两平台完成一次真实外部路径审批流程。）

- [x] **原生组件完整性（AC9）**：正确 Windows Helper 可被唯一定位；错误架构、版本不匹配、内容篡改、未知路径和额外 stdout 均被拒绝，恢复正确组件后无需改模型配置。（验证：组件完整性/Python 后端测试通过，最终 wheel manifest 的版本、目标和 SHA-256 与 Helper 匹配。）

- [x] **结构化错误与会话恢复（AC10）**：不支持平台、未准备、权限拒绝、Shell 启动失败、命令非零退出、超时、组件错配和进程回收失败均有稳定代码和中文摘要；TUI 不显示未捕获堆栈，下一轮纯对话可继续。（验证：CLI/后端/TUI 参数测试通过；真实非零、超时及越界读写分别返回稳定结构化代码。）

- [ ] **macOS 行为保持（AC11）**：既有配置、Provider、纯文本、Agent Loop、Plan Mode、文件/命令工具、Seatbelt、审批、断网和超时行为全部保持。（验证：在 macOS runner 运行 `pytest -q` 与 Seatbelt 原生 smoke，不安装或查找 Windows Helper。）

- [x] **平台测试选择明确（AC12）**：Windows 只运行 Windows 原生场景并明确跳过 macOS 项；macOS 结果对称；缺少前置不会伪装成通过。（验证：默认 Python 回归明确跳过 8 个需 `--run-windows-native` 的测试，显式启用后 8 项全部通过；其余 2 个 skip 均带平台/权限原因；Rust 原生测试必须以 `--ignored` 显式执行。）

- [ ] **Windows 完整 TUI 会话（AC13）**：真实终端会话依次完成读、写、搜索、成功命令、非零命令和超时，工具行、摘要、最终回答与实际结果一致，并能正常退出。（验证：按端到端场景 E1 保存脱敏终端记录与工作区文件对比。）

- [x] **无永久权限或资源扩大（AC14）**：多轮调用前后，工作区外 ACL、AppContainer 状态、进程、句柄和临时目录无非预期变化；开发/正式组件来源可区分。（验证：连续真实 smoke 后事务日志 0、worker 进程 0、越界目标不变；Rust 新建/覆盖/移动/崩溃恢复 ACL 测试通过。）

- [x] **干净检出可复现（AC15）**：未参与实现的开发者按 Windows 文档完成依赖、Rust Helper 构建、setup、diagnose 和 smoke，不猜测命令、不以管理员身份日常运行。（验证：`verify_windows_clean_room.ps1` 将 Git 源码复制到全新 D 盘目录，未复制 venv/target/Helper/wheel；离线完成 locked release、`win_amd64` wheel、新 venv 安装、setup、READY 诊断及完整 AppContainer/边界 smoke。）

## 安全与架构集成

- [x] **AppContainer 默认拒绝能力**：worker 使用匹配 profile SID 和空网络 capability 启动，未采用无隔离回退或实验性 `CreateProcessInSandbox`。（验证：Rust 属性测试、真实 token SID 校验与本地网络拒绝 smoke 均通过。）

- [x] **固定 worker 入口**：Helper 只能启动已校验 Python 的 `-m mewcode.tool_worker`，协议不能指定任意 executable arguments、环境或 capability。（验证：Rust runner/protocol 测试拒绝额外字段和非 Python 入口，真实 worker 使用固定模块启动。）

- [x] **Job 先绑定后执行**：worker 以 suspended 状态创建，加入独立 Job 后才恢复；Helper 句柄关闭会杀死完整进程树。（验证：真实 suspended AppContainer 启动、token 复核、Job 属性测试及超时后 worker 进程 0。）

- [x] **ACL 事务精确回滚**：每次调用只添加所需 ACE，结束时只移除同事务指纹 ACE，不覆盖并发或用户 DACL 变化。（验证：Rust ACL apply/并发/覆盖/移动/新建后代原生测试全部通过，真实 smoke 事务目录为空。）

- [x] **ACL 崩溃恢复失败关闭**：Helper 在授权后崩溃时，下次启动恢复其 ACE；文件 ID/指纹被篡改时不覆盖 DACL，状态变为 BROKEN 并阻止后续工具。（验证：Rust prepared crash recovery 原生测试和损坏/指纹篡改失败关闭测试通过；残留实测事务已恢复为 READY。）

- [x] **协议严格且版本化**：未知字段、非法类型、超限输入、截断 JSON、request ID 不一致、额外 stdout 和协议版本错配均被拒绝。（验证：Python `test_windows_protocol.py` 与 Rust protocol 测试全部通过。）

- [x] **TUI 持续响应**：诊断、长命令、取消和清理期间 Textual 事件循环持续刷新并响应操作；setup 不由 TUI、Agent 或模型自动触发。（验证：真实 WindowsSandbox 的 Textual `run_test` 会话在长命令期间响应 Escape，清理后继续纯对话并响应 Ctrl+C；启动只诊断，不自动 setup。）

- [x] **共享核心无平台分叉**：Provider、Conversation、工具 schema、审批结果和 `ToolResult` 在两平台一致，Windows FFI 只位于 Rust Helper 边界。（验证：Python 全量契约回归通过；Windows 使用相同 tool worker 和 ToolResult，FFI 保持在 Rust crate。）

## 构建与自动化测试

- [x] **Python 编译**：全部 Python 实现、测试和脚本可编译。（验证：`python -m compileall -q mewcode tests scripts`，退出码 0。）

- [x] **Python 全量测试**：平台无关测试及当前平台适用测试全部通过，没有通过删除、降级断言或隐藏 skip 获得绿色结果。（验证：默认回归为 141 passed、10 skipped、0 failed；显式 Windows 原生回归为 8 passed、0 failed，skip 原因见本页验收记录。）

- [x] **Rust 格式和静态检查**：Helper 通过格式与 clippy 严格检查。（验证：`cargo fmt --check` 与 `cargo clippy --all-targets -- -D warnings` 均退出码 0。）

- [x] **Rust 全量测试**：纯逻辑测试全部通过；Windows 原生 ignored 测试在受支持 runner 显式执行并通过。（验证：`cargo test` 退出码 0；`cargo test -- --ignored --test-threads=1` 的 14 个原生测试全部通过。）

- [x] **可复现 Helper 构建**：locked release 构建成功，产物为 PE x86-64，manifest 的应用/协议/Helper 版本和 SHA-256 与实际一致。（验证：`build_windows_helper.ps1 -Offline -Wheel` 成功，生成 `win_amd64` wheel；包内容和 manifest SHA-256 独立校验通过。）

- [x] **工作区质量检查**：只包含规格允许的源码、测试、构建配置和文档改动，不含密钥、用户配置、Rust target、生成 exe 或无关文件。（验证：`git diff --check` 通过；Git 状态已逐项核对；安装临时脚本、结果、旧 F 盘占位和 build 目录已清理，生成产物均由 `.gitignore` 排除；未提交或推送。）

## 端到端场景

- [ ] **E1：Windows 首次准备与完整工具链（AC1、AC3、AC13）**：在未准备的普通用户环境启动同一 MewCode，观察纯对话可用、工具提示 setup；显式准备后重启，要求 Agent 读取项目元数据、写入临时工作区文件、搜索并执行 PowerShell 核对，最后正常退出。（验证：保存 diagnose 前后状态、终端工具行、最终答复与文件内容；测试文件必须位于专用临时工作区。）

- [x] **E2：Windows 安全边界（AC2、AC6、AC7、AC8）**：在真实会话中尝试未授权外部读写、重解析点逃逸、本地端口联网和读取测试秘密，均失败；随后批准单文件仅本次只读，当前读取成功但同目录、写入和下一次调用仍失败。（验证：实际 AppContainer 原生边界测试、单文件授权测试与真实 TUI 审批键盘流程组合覆盖；外部文件、监听端和注入秘密均保持不可见/不变。）

- [x] **E3：Windows 超时、取消与恢复（AC5、AC14）**：命令创建后代进程并持续运行，先触发超时，再运行一次并取消；两次都显示结构化结果、无残留进程/ACL，随后普通命令和纯对话成功，Ctrl+C 正常退出。（验证：真实 AppContainer 三层进程树超时、WindowsSandbox 取消、宿主强退与真实 Textual 会话组合通过；事务为 0、后续命令/对话成功。）

- [x] **E4：组件篡改与诊断恢复（AC9、AC10）**：用测试夹具替换为错架构、错版本和篡改 Helper，应用逐项拒绝且不执行工具；恢复正确产物后 diagnose 为 READY，工具恢复。（验证：在隔离复制组件中依次验证内容篡改、PE ARM64 错架构且同步伪造哈希、Helper 版本错配；均被正式解析器拒绝，恢复原件后 diagnose READY。）

- [ ] **E5：macOS 回归（AC1、AC4、AC8、AC11）**：在 tmux 启动同版本 MewCode，完成纯对话、POSIX 命令、文件工具、一次外部审批、拒绝联网、命令超时和正常退出；无 Windows setup 或组件要求。（验证：保存脱敏 tmux 输出和 Seatbelt 诊断结果。）

## 验收完成条件

- [ ] spec AC1–AC15 每项至少对应一条已通过证据，且没有未解释失败。（验证：逐项核对本 checklist 中的 AC 标注与验收报告。）
- [ ] Python、Rust、构建完整性及两平台适用测试全部通过；skip 均有可解释的平台原因。（验证：附上实际命令、退出码和测试汇总。）
- [ ] E1–E5 全部完成；临时工作区由测试夹具安全清理，未改动用户文件或正式组件。（验证：核对场景记录和清理前后的限定路径。）
- [x] 验收报告记录实际结果、证据、通过数、失败数和剩余风险，不以预期代替观察。（验证：见 `acceptance-report.md`；自动化、Windows 原生安全、TUI、干净环境与未完成的真实终端/macOS 证据分开记录。）
