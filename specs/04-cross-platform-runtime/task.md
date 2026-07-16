# MewCode 跨平台执行与 Windows 原生支持 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `mewcode/sandbox/base.py` | 统一诊断接口与平台后端选择 |
| 修改 | `mewcode/sandbox/models.py` | 平台状态、诊断结果和原生授权类型 |
| 修改 | `mewcode/sandbox/__init__.py` | 导出新增公共类型 |
| 修改 | `mewcode/sandbox/macos.py` | Seatbelt 诊断、取消和结果解析对齐 |
| 修改 | `mewcode/sandbox/unavailable.py` | 统一失败关闭诊断 |
| 重写 | `mewcode/sandbox/windows.py` | Windows Helper 生命周期、超时和错误映射 |
| 新建 | `mewcode/sandbox/windows_protocol.py` | Python/Rust 严格 JSON 协议 |
| 新建 | `mewcode/sandbox/native_components.py` | Helper manifest、PE、版本和 SHA-256 校验 |
| 新建 | `mewcode/shell.py` | Windows/macOS Shell 与最小环境适配 |
| 修改 | `mewcode/tools/command.py` | 移除 `shell=True` 和 POSIX 专用进程控制 |
| 修改 | `mewcode/prompts.py` | 平台与 Shell 上下文 |
| 修改 | `mewcode/agent.py` | 普通模式与 Plan Mode 注入平台上下文 |
| 修改 | `mewcode/cli.py` | `sandbox diagnose/setup` 管理命令 |
| 修改 | `mewcode/tool_worker.py` | 单行 UTF-8 JSON 与错误边界 |
| 修改 | `mewcode/tui/app.py` | 启动诊断与工具不可用状态 |
| 修改 | `mewcode/tui/screens.py`、`widgets.py` | 沙箱状态和恢复提示 |
| 新建 | `native/windows-sandbox-helper/Cargo.toml`、`Cargo.lock` | Rust Helper 工程与锁定依赖 |
| 新建 | `native/windows-sandbox-helper/src/*.rs` | 协议、profile、路径、ACL、AppContainer、Job 与 Runner |
| 新建 | `native/windows-sandbox-helper/tests/*.rs` | Rust 逻辑与 Windows 原生集成测试 |
| 新建 | `scripts/build_windows_helper.ps1` | Release 构建、manifest、哈希与包资源生成 |
| 修改 | `pyproject.toml` | 平台资源、测试 marker 与打包元数据 |
| 修改 | `.gitignore` | Rust target 和生成二进制忽略规则 |
| 新建 | `README.md`、`docs/development.md` | 支持矩阵、构建、测试与 E2E 文档 |
| 新建/修改 | `tests/test_*.py`、`tests/windows/*.py` | Python 单元、跨层、Windows E2E 与回归测试 |

## T1：确认基线与 Windows 原生构建前置

**文件：** 不修改功能文件；记录命令输出供后续对比

**依赖：** 无

**步骤：**

1. 确认当前 Python、Windows 版本、处理器架构和 NTFS 工作区。
2. 运行现有完整测试，记录已知 Windows 失败，不能把失败测试删除或跳过。
3. 检查 `cargo`、`rustc`、MSVC linker 和 Windows SDK；缺失时停止并请求用户批准安装受信 Rust stable + MSVC 环境。
4. 记录当前 Git 状态，只处理本规格相关文件。

**验证：** 运行 `python --version`、`cargo --version`、`rustc --version`、`pytest -q`；前置齐全后版本命令均成功，测试基线与开始开发前记录一致。

## T2：增加统一沙箱诊断模型

**文件：** `mewcode/sandbox/models.py`、`base.py`、`__init__.py`、`tests/test_sandbox_backends.py`

**依赖：** T1

**步骤：**

1. 实现 `SandboxState` 和不可变 `SandboxDiagnostic`，字段与 plan.md 一致。
2. 为 `Sandbox` 增加同步只读 `diagnose()` 抽象方法。
3. 让 `SandboxFactory` 保持按系统选择后端，不执行 setup 或无隔离回退。
4. 更新测试替身和导出，验证 Darwin、Windows、Linux、未知平台映射。

**验证：** 运行 `pytest -q tests/test_sandbox_backends.py -k 'factory or diagnostic'`，期望全部通过。

## T3：对齐不可用与 macOS 后端诊断

**文件：** `mewcode/sandbox/unavailable.py`、`mewcode/sandbox/macos.py`、`tests/test_sandbox_backends.py`

**依赖：** T2

**步骤：**

1. `UnavailableSandbox` 返回稳定 `UNSUPPORTED/BROKEN` 诊断并继续拒绝运行。
2. `MacOSSandbox.diagnose()` 检查 `sandbox-exec`，区分 ready 与 component missing。
3. macOS 运行时复用统一诊断，不改变现有 Seatbelt profile 权限。
4. 增加存在、缺失和运行失败测试。

**验证：** 运行 `pytest -q tests/test_sandbox_backends.py -k 'macos or unavailable'`，期望全部通过。

## T4：实现平台 Shell 规范

**文件：** `mewcode/shell.py`、`tests/test_shell.py`

**依赖：** T1

**步骤：**

1. 定义冻结的 `ShellSpec` 与当前平台选择入口。
2. Windows 只从系统目录解析 Windows PowerShell，并固定无 profile、非交互参数。
3. macOS 固定返回 `/bin/sh -c`；不支持平台返回明确异常。
4. 实现最小环境与 UTF-8 编码设置，排除代理和凭据变量。

**验证：** 运行 `pytest -q tests/test_shell.py`，期望平台选择、固定路径、参数和环境清洗测试全部通过。

## T5：重构命令工具为显式 Shell

**文件：** `mewcode/tools/command.py`、`tests/test_tools_command.py`

**依赖：** T4

**步骤：**

1. 用 `ShellSpec` 参数数组启动命令，删除 `shell=True`。
2. 保持 cwd、stdout、stderr、退出码、截断和结构化错误契约。
3. 删除 Windows 不支持的 `os.killpg` 路径；保留宿主沙箱之外的有界防御性清理。
4. 分别添加 PowerShell 连接/错误流/中文 UTF-8 和 POSIX 语法测试。

**验证：** 在 Windows 运行 `pytest -q tests/test_tools_command.py tests/test_shell.py`，期望全部通过且不再出现 `os.killpg` 错误。

## T6：向模型注入平台与 Shell 上下文

**文件：** `mewcode/prompts.py`、`mewcode/agent.py`、`tests/test_prompts.py`、`tests/test_agent.py`

**依赖：** T4

**步骤：**

1. 从 Shell 适配层生成不含路径和敏感环境的平台描述。
2. 普通模式每轮 system prompt 只追加一次当前平台/Shell。
3. Plan Mode 在相同上下文上叠加既有只读提示，不改变工具集合规则。
4. 添加 Windows、macOS、普通模式和 Plan Mode 测试。

**验证：** 运行 `pytest -q tests/test_prompts.py tests/test_agent.py -k 'prompt or plan'`，期望全部通过。

## T7：定义 Windows Helper Python 协议类型

**文件：** `mewcode/sandbox/windows_protocol.py`、`tests/test_windows_protocol.py`

**依赖：** T2

**步骤：**

1. 实现 `NativeGrant`、`WindowsRunRequest`、`RunnerStatus`、`RunnerError` 和 `WindowsRunResponse`。
2. 实现固定字段的单行 UTF-8 JSON 序列化与严格反序列化。
3. 拒绝未知字段、版本不匹配、相对/NUL 路径、非法超时、request ID 不一致和超限消息。
4. 将合法 worker result 复用为现有 `ToolResult`。

**验证：** 运行 `pytest -q tests/test_windows_protocol.py`，期望合法 round-trip 与全部恶意/截断输入测试通过。

## T8：实现原生组件 manifest 与路径约束

**文件：** `mewcode/sandbox/native_components.py`、`tests/test_native_components.py`

**依赖：** T2

**步骤：**

1. 实现 `NativeComponentManifest` 读取和必填字段/类型校验。
2. 正式模式只接受包内固定目录，开发模式只接受固定 Rust release 目录。
3. 规范化绝对路径并拒绝工作区同名程序、PATH 搜索和重解析越界。
4. 为缺失、未知路径、开发标识错误和版本错误返回稳定诊断。

**验证：** 运行 `pytest -q tests/test_native_components.py -k 'manifest or path or version'`，期望全部通过。

## T9：增加 PE 架构、SHA 与 Helper 自报校验

**文件：** `mewcode/sandbox/native_components.py`、`tests/test_native_components.py`

**依赖：** T7、T8

**步骤：**

1. 解析 PE 头并只接受 Windows x86-64 目标。
2. 流式计算 Helper SHA-256，与 manifest 做常量时间比较。
3. 调用固定 Helper `version` 操作，核对协议与 Helper 版本。
4. 覆盖篡改、错误架构、额外 stdout、超时和自报不匹配。

**验证：** 运行 `pytest -q tests/test_native_components.py`，期望完整性测试全部通过。

## T10：建立 WindowsSandbox 编排骨架

**文件：** `mewcode/sandbox/windows.py`、`tests/test_sandbox_windows.py`

**依赖：** T7、T9

**步骤：**

1. 实现 `diagnose()`，聚合平台支持、组件完整性和 Helper diagnose 响应。
2. 将 `SandboxRequest` 转换为固定 `WindowsRunRequest`。
3. 用 `asyncio.create_subprocess_exec` 启动 Helper 并传输单条 JSON。
4. 用可控假 Helper 覆盖 ready、setup required、协议失败和进程启动失败。

**验证：** 运行 `pytest -q tests/test_sandbox_windows.py -k 'diagnose or request or start'`，期望全部通过。

## T11：实现 WindowsSandbox 超时与取消监督

**文件：** `mewcode/sandbox/windows.py`、`tests/test_sandbox_windows.py`

**依赖：** T10

**步骤：**

1. 为 Helper 通信设置比 Helper 内部截止时间略晚的监督超时。
2. asyncio 取消时关闭 stdin、终止并有界等待 Helper，再重新抛出 `CancelledError`。
3. 对 Helper 卡死、非零退出和无响应返回稳定错误，不执行宿主重试。
4. 断言测试结束后没有挂起任务或残留假 Helper。

**验证：** 运行 `pytest -q tests/test_sandbox_windows.py -k 'timeout or cancel or cleanup'`，期望全部通过且无 asyncio 资源告警。

## T12：增加沙箱诊断与准备 CLI

**文件：** `mewcode/cli.py`、`tests/test_cli.py`

**依赖：** T2、T10

**步骤：**

1. 保持无参数 `mewcode` 启动现有 TUI。
2. 增加 `mewcode sandbox diagnose`，输出后端、状态、错误码和修复建议。
3. 增加显式 `mewcode sandbox setup`，只在 Windows 调用已校验 Helper。
4. 保证 Agent/工具无法调用 setup，CLI 不原样打印敏感路径或 Win32 异常。

**验证：** 运行 `pytest -q tests/test_cli.py -k sandbox`，期望 ready、setup required、broken、unsupported 和 setup 失败测试全部通过。

## T13：接入 TUI 沙箱状态

**文件：** `mewcode/tui/app.py`、`screens.py`、`widgets.py`、`tests/test_app.py`、`tests/test_screens.py`

**依赖：** T2、T12

**步骤：**

1. TUI 启动时异步读取诊断，不阻塞 Textual 事件循环。
2. 状态栏显示后端与简短状态，setup required/broken 提供管理命令提示。
3. 沙箱不可用时保留纯对话，工具调用继续由后端结构化拒绝。
4. 增加 ready、不可用、诊断异常和纯对话回归测试。

**验证：** 运行 `pytest -q tests/test_app.py tests/test_screens.py -k 'sandbox or conversation'`，期望全部通过。

## T14：创建 Rust Helper 工程与稳定协议

**文件：** `native/windows-sandbox-helper/Cargo.toml`、`Cargo.lock`、`src/main.rs`、`src/protocol.rs`、`tests/protocol.rs`

**依赖：** T1、T7

**步骤：**

1. 创建只面向 Windows x86-64 的 Rust binary crate，锁定最小 serde/Windows API 依赖。
2. 定义 `version`、`diagnose`、`setup`、`run` 四种操作及稳定响应 envelope。
3. stdin 只读取一条限长 JSON，stdout 只写一条 JSON；日志只写 stderr。
4. 未知操作、字段、版本和多余输入均返回协议错误。

**验证：** 运行 `cargo test --manifest-path native/windows-sandbox-helper/Cargo.toml --test protocol`，期望全部通过。

## T15：实现版本化状态文件与原子写入

**文件：** `native/windows-sandbox-helper/src/state.rs`、`src/diagnostics.rs`、对应 Rust 测试

**依赖：** T14

**步骤：**

1. 定义 `SandboxSetupState` 与事务目录布局。
2. 从固定产品 ID 和当前用户 SID 派生 install ID。
3. 使用同目录临时文件、flush 和原子替换写 `state.json`。
4. 拒绝未知 schema、所有者不匹配和损坏状态，并输出脱敏错误。

**验证：** 运行 `cargo test --manifest-path native/windows-sandbox-helper/Cargo.toml state`，期望幂等、损坏和中断写入测试通过。

## T16：实现 AppContainer profile 查询与稳定命名

**文件：** `native/windows-sandbox-helper/src/profile.rs`、对应 Rust 测试

**依赖：** T15

**步骤：**

1. 以固定命名规则派生 profile 名称，禁止工作区或模型输入参与。
2. 查询已存在 profile 并转换/核对 AppContainer SID。
3. 区分缺失、匹配和同名异常三种状态。
4. 为 SID 内存和 Win32 buffer 建立 RAII 清理。

**验证：** 运行 `cargo test --manifest-path native/windows-sandbox-helper/Cargo.toml profile`，期望命名和查询测试通过。

## T17：实现 AppContainer profile 幂等准备

**文件：** `native/windows-sandbox-helper/src/profile.rs`、`src/main.rs`、对应 Rust 测试

**依赖：** T16

**步骤：**

1. 缺失时用 Windows API 创建 profile，存在且匹配时不重复创建。
2. 创建后重新查询并核对 SID，再写状态文件。
3. 同名异常、企业策略拒绝和部分失败时不删除/接管未知 profile。
4. setup 完成后调用同一 diagnose 逻辑，只有 READY 才成功。

**验证：** 在 Windows 运行 `cargo test --manifest-path native/windows-sandbox-helper/Cargo.toml setup_profile -- --ignored`，首次与重复执行均通过且 profile 数量不增加。

## T18：实现 Windows 路径与卷策略

**文件：** `native/windows-sandbox-helper/src/paths.rs`、`tests/path_policy.rs`

**依赖：** T14

**步骤：**

1. 支持盘符、反斜杠、空格和大小写不敏感比较。
2. 通过句柄获取最终规范路径、卷类型、文件系统和文件 ID。
3. 拒绝 UNC、映射网络盘、非 NTFS、相对/NUL 路径和不存在的必要根。
4. 对文件与目录重新推导 kind，不信任 Python 分类。

**验证：** 运行 `cargo test --manifest-path native/windows-sandbox-helper/Cargo.toml --test path_policy`，期望合法路径及所有拒绝路径测试通过。

## T19：阻断重解析点与审批后替换

**文件：** `native/windows-sandbox-helper/src/paths.rs`、`tests/path_policy.rs`

**依赖：** T18

**步骤：**

1. 检查祖先和目标的重解析属性及最终句柄路径。
2. 在写 ACL 前记录卷序列与文件 ID，启动 worker 前重新核对。
3. 对 junction、symlink、挂载点、目标替换和跨卷解析返回稳定错误。
4. 验证单文件授权不会扩展到同目录其他文件。

**验证：** 以具备创建链接权限的 Windows 测试环境运行 `cargo test --manifest-path native/windows-sandbox-helper/Cargo.toml reparse -- --ignored`，期望所有逃逸场景被拒绝。

## T20：定义 ACL 事务日志与 ACE 指纹

**文件：** `native/windows-sandbox-helper/src/acl.rs`、`src/state.rs`、对应 Rust 测试

**依赖：** T15、T18

**步骤：**

1. 定义版本化 `AclEntryRecord`、`AclTransactionJournal` 和状态机。
2. 根据 SID、路径文件 ID、rights、inheritance 和事务 ID 生成稳定指纹。
3. 每次更新使用原子日志写入，记录 owner PID 与调用 ID。
4. 解析损坏或未知版本日志时失败关闭。

**验证：** 运行 `cargo test --manifest-path native/windows-sandbox-helper/Cargo.toml acl_journal`，期望序列化、指纹和损坏日志测试通过。

## T21：实现最小 ACL 添加与精确回滚

**文件：** `native/windows-sandbox-helper/src/acl.rs`、对应 Rust 测试

**依赖：** T19、T20

**步骤：**

1. 把 read/write/execute 映射为最小 NTFS rights 与继承范围。
2. 先持久化事务，再向 AppContainer SID 添加带事务标识的 ACE。
3. 回滚时重新读取当前 DACL，只删除文件 ID 和指纹匹配的本事务 ACE。
4. 保留用户与并发事务在调用期间产生的其他 DACL 变化。

**验证：** 在临时 NTFS 目录运行 `cargo test --manifest-path native/windows-sandbox-helper/Cargo.toml acl_apply -- --ignored`，期望授权生效、精确回滚且原有 ACL 不变。

## T22：实现 ACL 崩溃恢复与并发事务

**文件：** `native/windows-sandbox-helper/src/acl.rs`、`src/state.rs`、对应 Rust 测试

**依赖：** T21

**步骤：**

1. 启动时锁定并扫描未完成事务，按记录恢复。
2. 文件 ID 或 ACE 指纹不匹配时不写 DACL，标记 BROKEN 并给出人工修复码。
3. 支持多个事务向同一路径授权并分别移除自己的 ACE。
4. 清理全部成功后原子标记完成并删除事务临时状态。

**验证：** 运行 `cargo test --manifest-path native/windows-sandbox-helper/Cargo.toml acl_recovery -- --ignored`，期望崩溃、篡改和并发场景全部通过。

## T23：构造无网络 AppContainer 启动属性

**文件：** `native/windows-sandbox-helper/src/appcontainer.rs`、对应 Rust 测试

**依赖：** T16

**步骤：**

1. 从已校验 profile SID 构造 `SECURITY_CAPABILITIES`。
2. capability 列表保持为空，不授予 internetClient、privateNetworkClientServer 等网络能力。
3. 建立 `STARTUPINFOEX` 属性列表的 RAII 分配和释放。
4. 检查所有 Win32 返回值并映射为脱敏稳定错误。

**验证：** 运行 `cargo test --manifest-path native/windows-sandbox-helper/Cargo.toml appcontainer_attributes`，期望属性构造、空 capability 和清理测试通过。

## T24：实现 Job Object RAII 与进程树终止

**文件：** `native/windows-sandbox-helper/src/job.rs`、对应 Rust 测试

**依赖：** T14

**步骤：**

1. 创建每调用独立 Job Object 并设置 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`。
2. 提供只接受 suspended 进程句柄的 assign 操作。
3. 关闭 Job 时等待进程树在限定时间退出并释放句柄。
4. 覆盖子进程、孙进程、assign 失败和重复关闭。

**验证：** 在 Windows 运行 `cargo test --manifest-path native/windows-sandbox-helper/Cargo.toml job_tree -- --ignored`，期望关闭 Job 后全部测试进程消失。

## T25：以 suspended AppContainer 进程启动固定 worker

**文件：** `native/windows-sandbox-helper/src/runner.rs`、`src/appcontainer.rs`、`src/job.rs`

**依赖：** T21、T23、T24

**步骤：**

1. 只接受已校验 Python executable，固定命令为 `-m mewcode.tool_worker`。
2. 用扩展启动信息和 `CREATE_SUSPENDED` 创建 AppContainer worker。
3. 在恢复主线程前把进程加入 Job；任何失败都终止 suspended 进程。
4. 构造最小环境块，不传 Provider key、token、proxy 或用户配置变量。

**验证：** 运行 `cargo test --manifest-path native/windows-sandbox-helper/Cargo.toml suspended_worker -- --ignored`，期望 worker 仅在加入 Job 后运行且环境测试通过。

## T26：实现 Helper 与 tool_worker 的受限 I/O

**文件：** `native/windows-sandbox-helper/src/runner.rs`、`mewcode/tool_worker.py`、对应 Rust/Python 测试

**依赖：** T25

**步骤：**

1. 向 worker stdin 写一条限长 JSON 并关闭输入端。
2. 并发限长收集 stdout/stderr，stdout 只接受一个完整 `ToolResult`。
3. stderr 做长度限制和脱敏，不原样进入模型结果。
4. worker 崩溃、额外 stdout、截断 JSON 和 request ID 不匹配均返回协议错误。

**验证：** 运行 `cargo test --manifest-path native/windows-sandbox-helper/Cargo.toml worker_io` 与 `pytest -q tests/test_tool_worker.py`，期望全部通过。

## T27：实现 Helper 超时、取消和宿主断开清理

**文件：** `native/windows-sandbox-helper/src/runner.rs`、`src/job.rs`、`src/acl.rs`

**依赖：** T22、T26

**步骤：**

1. Helper 以请求 `timeout_ms` 作为最终执行截止时间。
2. 超时、宿主 stdin 断开或终止请求时先关闭 Job，再回滚 ACL。
3. 进程或 ACL 清理失败覆盖成功 worker 结果，返回稳定失败。
4. 确保所有句柄、管道和临时目录在 finally 路径释放。

**验证：** 运行 `cargo test --manifest-path native/windows-sandbox-helper/Cargo.toml runner_cleanup -- --ignored`，期望 timeout、cancel、host exit 和清理失败测试通过。

## T28：串联 Helper diagnose/setup/run

**文件：** `native/windows-sandbox-helper/src/main.rs`、`src/diagnostics.rs`、上述 Rust 模块

**依赖：** T17、T19、T22、T27

**步骤：**

1. diagnose 只读检查版本、profile、状态与残留事务。
2. setup 串联 profile 幂等创建、状态写入与 READY 复检。
3. run 固定执行路径校验、ACL、Job、AppContainer worker、清理顺序。
4. 所有操作只输出稳定 JSON，退出码与响应状态一致。

**验证：** 运行 `cargo test --manifest-path native/windows-sandbox-helper/Cargo.toml`，期望单元测试全部通过；需原生权限的测试仅以明确 ignored 状态列出。

## T29：构建、生成 manifest 并接入包资源

**文件：** `scripts/build_windows_helper.ps1`、`pyproject.toml`、`.gitignore`、`mewcode/native/windows-x86_64/*`

**依赖：** T9、T28

**步骤：**

1. 脚本执行 `cargo build --locked --release`，拒绝未锁定依赖。
2. 从构建产物生成协议/应用/Helper/架构/development 字段与 SHA-256。
3. 把 exe 与 manifest 放到固定包资源目录，生成产物由 Git 忽略，`Cargo.lock` 提交跟踪。
4. 让 setuptools 在 Windows 构建中包含对应资源，不让 macOS 运行依赖 Windows 文件。

**验证：** 运行 `powershell -ExecutionPolicy Bypass -File scripts/build_windows_helper.ps1`，随后运行 `pytest -q tests/test_native_components.py`，期望正式解析器接受生成产物。

## T30：用真实 Helper 完成 WindowsSandbox 集成

**文件：** `mewcode/sandbox/windows.py`、`tests/test_sandbox_windows.py`

**依赖：** T11、T29

**步骤：**

1. 用正式组件解析结果启动真实 Helper，不绕过 manifest 校验。
2. 映射 diagnose/setup/run 的稳定状态与错误码。
3. 合法响应转换为现有 `ToolResult`，Provider/Agent/工具 schema 不改变。
4. 验证组件替换、版本错配和 Helper 异常不会触发宿主执行。

**验证：** 在 Windows 运行 `pytest -q tests/test_sandbox_windows.py --run-windows-native`，期望真实 Helper 集成测试通过。

## T31：验证工作区工具与 PowerShell 契约

**文件：** `tests/windows/test_appcontainer_e2e.py`

**依赖：** T5、T30

**步骤：**

1. 在临时 NTFS 工作区通过真实 `WindowsSandbox` 执行读、写、找文件和搜索。
2. 执行 PowerShell 变量、连接、stdout/stderr、非零退出和中文 UTF-8 场景。
3. 确认所有修改只出现在授权工作区/专用临时目录。
4. 每个调用后检查 Helper 与 worker 进程已退出。

**验证：** 运行 `pytest -q tests/windows/test_appcontainer_e2e.py -k 'workspace or command' --run-windows-native`，期望全部通过。

## T32：验证路径与外部授权边界

**文件：** `tests/windows/test_appcontainer_e2e.py`、`tests/test_sandbox_permissions.py`

**依赖：** T19、T30

**步骤：**

1. 覆盖空格、大小写差异、盘符、单文件、目录、只读和写入授权。
2. 未授权工作区外读取/创建必须失败且目标保持不变。
3. junction/symlink/审批后替换不得逃逸到外部真实目标。
4. 一次/会话授权沿用现有语义，应用重启后不保留。

**验证：** 运行 `pytest -q tests/test_sandbox_permissions.py tests/windows/test_appcontainer_e2e.py -k 'path or grant or reparse' --run-windows-native`，期望全部通过。

## T33：验证默认断网与敏感环境清洗

**文件：** `tests/windows/test_appcontainer_e2e.py`、相关 Provider 测试

**依赖：** T25、T30

**步骤：**

1. 向宿主注入假的 API key、token 和 proxy 值，工具环境中不得出现。
2. worker/子进程连接本地监听端口与外部地址均失败。
3. 主应用 mock Provider 请求仍成功，证明网络限制只作用于工具边界。
4. 捕获日志和结果，确认不存在注入的敏感值。

**验证：** 运行 `pytest -q tests/windows/test_appcontainer_e2e.py -k 'network or environment' tests/test_openai.py tests/test_anthropic.py --run-windows-native`，期望全部通过。

## T34：验证超时、取消和完整进程树回收

**文件：** `tests/windows/test_appcontainer_e2e.py`、`tests/test_tool_executor.py`

**依赖：** T27、T30

**步骤：**

1. 让命令创建父、子和孙进程并保持运行。
2. 分别触发 Helper 超时、Agent 取消和 Python 宿主中断。
3. 在限定时间内核对整棵进程树消失、ACL 已回滚。
4. 立即执行下一条命令并正常退出应用。

**验证：** 运行 `pytest -q tests/windows/test_appcontainer_e2e.py -k 'timeout or cancel or process_tree' --run-windows-native`，期望全部通过且无残留测试进程。

## T35：验证 ACL 崩溃恢复与并发清理

**文件：** `tests/windows/test_acl_recovery_e2e.py`

**依赖：** T22、T30

**步骤：**

1. 在添加 ACE 后强制终止 Helper，确认 Job 清理 worker 且事务日志保留。
2. 下次 diagnose/run 自动恢复只属于该事务的 ACE。
3. 并发只读调用同一路径时，一个结束不影响另一个。
4. 篡改 ACE/替换目标时进入 BROKEN 且不覆盖当前 DACL。

**验证：** 运行 `pytest -q tests/windows/test_acl_recovery_e2e.py --run-windows-native`，期望恢复、并发和失败关闭场景全部通过。

## T36：补齐跨平台开发与发布文档

**文件：** `README.md`、`docs/development.md`

**依赖：** T12、T29、T31

**步骤：**

1. 说明同一产品/版本、macOS 13+、Windows 10 1809+/11 x86-64 支持矩阵。
2. 分别写 Python 依赖、Rust/MSVC 前置、Helper 构建、setup、diagnose 和测试命令。
3. 标注不支持 ARM64/Server/网络盘/非 NTFS，以及标准流程不需管理员日常运行。
4. 写真实终端 E2E、常见错误码和企业策略下修复边界，不包含本机绝对路径。

**验证：** 从干净 shell 按 `docs/development.md` 依次执行构建与 smoke 命令，期望无需猜测额外步骤并得到 READY。

## T37：运行 macOS 与平台无关回归

**文件：** 相关测试文件；只修复本规格引入的回归

**依赖：** T3、T5、T6、T13、T30

**步骤：**

1. 运行 Provider、Conversation、Agent、Plan Mode、工具与 TUI 测试。
2. 在 macOS runner 运行 Seatbelt 文件/命令/审批/断网/超时 smoke。
3. Linux/未知平台验证明确失败关闭并以原因跳过原生测试。
4. 确认现有配置无需 Windows 专用字段。

**验证：** 运行 `pytest -q`；在 macOS runner 运行平台原生测试，期望全部适用测试通过且跳过项有明确原因。

## T38：执行静态检查、全量测试与产物校验

**文件：** 本规格涉及的全部实现和测试文件

**依赖：** T31–T37

**步骤：**

1. 运行 Python 字节码编译和完整 pytest。
2. 运行 `cargo fmt --check`、`cargo clippy -- -D warnings` 和完整 Rust 测试。
3. 重新构建 release Helper，校验 manifest、PE x86-64 和 SHA-256。
4. 检查测试结束后的进程、ACL 事务、临时目录和 Git diff，不掩盖失败或资源告警。

**验证：** 运行 `python -m compileall -q mewcode tests`、`pytest -q`、`cargo fmt --manifest-path native/windows-sandbox-helper/Cargo.toml --check`、`cargo clippy --manifest-path native/windows-sandbox-helper/Cargo.toml -- -D warnings`、`cargo test --manifest-path native/windows-sandbox-helper/Cargo.toml`，期望全部成功。

## T39：执行真实终端跨平台验收

**文件：** 不修改功能文件；按批准的 `checklist.md` 记录证据

**依赖：** T38、已批准的 `checklist.md`

**步骤：**

1. Windows 用真实终端/ConPTY 启动 MewCode，macOS 用 tmux 启动同版本 MewCode。
2. 连续完成纯对话、读文件、写文件、搜索、成功命令、非零退出命令和超时命令。
3. 验证工作区外拒绝/一次授权/会话授权、敏感环境不可见和默认断网。
4. 通过 `/exit` 与 Ctrl+C 正常退出，检查无残留进程、临时 ACL 或未清理文件。

**验证：** 对照已批准 `checklist.md` 保存脱敏的命令、输出和状态证据；所有适用条目通过后才完成任务。

## 执行顺序

```text
T1
├─→ T2 → T3
├─→ T4 → T5 → T6
└─→ T14 → T15 → T16 → T17

T2 → T7 → T8 → T9 → T10 → T11 → T12 → T13
T14 ───────────────┬→ T18 → T19 ─┬→ T20 → T21 → T22 ─┐
T16 → T17 ─────────┤              ├→ T23 ──────────────┤
T14 ───────────────┴──────────────┴→ T24 ──────────────┤
T21 + T23 + T24 → T25 → T26 → T27                       │
T17 + T19 + T22 + T27 → T28 → T29                       │
T11 + T29 → T30 ────────────────────────────────────────┘

T30 → T31 → T32
T30 → T33
T30 → T34
T30 → T35
T12 + T29 + T31 → T36
T3 + T5 + T6 + T13 + T30 → T37
T31–T37 → T38 → T39
```
