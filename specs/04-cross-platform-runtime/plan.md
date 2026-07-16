# MewCode 跨平台执行与 Windows 原生支持 Plan

## 架构概览

本阶段继续使用现有 Python 主程序作为唯一产品核心。Agent Loop、Provider、工具协议、权限审批和 TUI 不按操作系统复制；平台差异收敛在三处：宿主沙箱后端、命令 Shell/进程控制、原生组件发现与诊断。

Windows 增加一个独立 Rust 原生 Helper，承担 Python 标准库无法可靠完成的安全边界。Helper 提供准备、诊断和运行三个入口：准备入口由用户显式执行，为当前宿主用户创建或校验稳定的 AppContainer profile；诊断入口只读检查平台、组件、profile、状态与版本；运行入口在日常普通用户会话中接收一项 `SandboxRequest`，建立临时文件权限、AppContainer 安全能力和 Job Object，再启动现有 Python `tool_worker`。Python 不直接调用底层 Windows 安全 API，也不持有可被模型工具访问的沙箱凭据。

Windows 的隔离身份采用 AppContainer。Helper 通过固定产品 ID 与当前宿主用户 SID 派生稳定的 profile 名称，并使用 profile 的 AppContainer SID 作为全部文件授权主体；不创建本地账户、不保存密码，也不依赖按用户维护的防火墙规则。工作进程使用 `STARTUPINFOEX` 与 `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES` 在 AppContainer 中启动，不授予任何网络 capability，使网络访问保持默认拒绝；AppContainer 同时隔离用户凭据、进程与窗口对象。微软近期提供的 `Experimental_CreateProcessInSandbox` 不作为当前实现基线，因为它仍是实验 API，无法覆盖 Windows 10 1809+ 的稳定兼容目标。

文件边界使用按调用建立的 ACL 事务。Helper 根据 `SandboxRequest.grants` 计算运行时只读根、工作区/临时目录读写根和本次外部授权，在启动工作进程前只为沙箱 SID 添加所需权限，并把每条新增 ACE 的目标、继承范围和指纹写入受保护事务日志；调用结束、超时或取消时只移除本事务拥有的 ACE，不覆盖调用期间发生的其他 ACL 变化。下一次启动先清理未完成事务，避免崩溃留下权限扩大。路径在 Python 审批层和 Rust Helper 内各自重新规范化；Helper 拒绝 UNC、网络盘、非 NTFS 工作区以及越界重解析点，不能把 Python 传入的路径当作可信结果。

每次 Windows 工具调用都由 Helper 创建独立 Job Object，并设置关闭句柄即终止全部关联进程。`tool_worker` 与其启动的 PowerShell、编译器、测试进程共享同一 Job；外层超时、Agent 取消或 Python 主进程终止都会关闭 Runner/Job，使整棵进程树退出。工作进程继续使用当前单行 JSON 输入输出协议，Windows Helper 只增加一层原生执行信封，因此 Provider、Agent 和工具结果格式不变。

命令工具不再使用隐式 `shell=True`。新增平台 Shell 适配层：Windows 明确调用系统 PowerShell 并以 UTF-8 捕获 stdout/stderr，macOS 明确调用 POSIX Shell。超时的最终裁决位于宿主沙箱进程层；命令层只负责参数、输出、退出码和平台命令行构造，避免 Windows 使用不存在的 POSIX 进程组 API。

原生组件加载器只接受两个受控来源：安装包内与 MewCode 版本/架构匹配的正式组件，或项目固定构建目录内带开发标识的本地组件。清单记录协议版本、应用版本、目标架构和 SHA-256；缺失、错架构、错版本、哈希变化或未知路径均失败关闭。默认 `mewcode` 仍启动 TUI，新增显式的沙箱诊断与准备命令，不让模型触发管理员操作。

```text
MewCode TUI / Agent / ToolExecutor（共享 Python 核心）
                     │ SandboxRequest + grants
          ┌──────────┴──────────┐
          │                     │
   macOS Sandbox          Windows Sandbox
   Seatbelt 后端          Python 组件加载/协议校验
          │                     │ JSON stdin/stdout
          │              Rust Windows Helper
          │        ┌────────────┼─────────────┐
          │    AppContainer    ACL 事务      Job Object
          │        └────────────┼─────────────┘
          └──────────────► Python tool_worker
                                  │
                        文件/搜索/平台 Shell
                                  │
                              ToolResult
```

安全边界面向不可信的模型生成命令及其子进程；宿主管理员和能够修改 MewCode 安装文件的本地攻击者不在本阶段威胁模型内。AppContainer 允许读取 Windows 明确提供给该隔离环境的系统资源以及请求中显式授权的运行时/工作区路径，不允许借此读取其他用户资源。任何 profile、ACL、AppContainer 启动、Job 或组件校验步骤失败，调用都返回结构化错误，不启动未隔离工作进程。

## 核心数据结构

### Python 平台状态

```python
class SandboxState(str, Enum):
    READY = "ready"
    SETUP_REQUIRED = "setup_required"
    UNSUPPORTED = "unsupported"
    BROKEN = "broken"

@dataclass(frozen=True)
class SandboxDiagnostic:
    state: SandboxState
    backend: str
    code: str
    message: str
    remediation: str = ""
    component_version: str | None = None
```

`SandboxDiagnostic` 是 CLI、TUI 和测试共用的只读状态。`message` 面向用户，`code` 保持稳定，`remediation` 只包含用户可执行的下一步，不携带原始环境变量或凭据。

`Sandbox` 接口扩展为：

```python
class Sandbox(ABC):
    @abstractmethod
    def diagnose(self) -> SandboxDiagnostic: ...

    @abstractmethod
    async def run(
        self,
        request: SandboxRequest,
        timeout: float,
    ) -> ToolResult: ...
```

任务取消继续使用 `asyncio` 任务取消语义，不再增加第二套取消令牌；每个后端必须在捕获 `CancelledError` 后终止宿主 Runner，再重新抛出取消，让 `ToolExecutor` 生成现有 `cancelled` 结果。

### 原生组件清单

```python
@dataclass(frozen=True)
class NativeComponentManifest:
    protocol_version: int
    app_version: str
    helper_version: str
    target: str
    sha256: str
    development: bool = False
```

正式组件目标值固定为 `windows-x86_64`；开发组件必须同时带 `development=true` 且位于项目固定构建目录。加载器对清单字段、文件名、绝对路径、PE 目标架构、Helper 自报协议版本和 SHA-256 全部校验后才返回可执行路径。

```python
class NativeComponentResolver:
    def resolve_windows_helper(self) -> Path: ...
    def diagnose_windows_helper(self) -> SandboxDiagnostic: ...
```

接口没有 PATH 搜索或用户自定义路径参数，防止工作区中的同名程序被加载。

### Python → Rust 运行请求

Python 通过 Helper stdin 写入一行 UTF-8 JSON：

```json
{
  "protocol_version": 1,
  "operation": "run",
  "request_id": "tool-call-id",
  "timeout_ms": 20000,
  "python_executable": "C:\\...\\python.exe",
  "workspace": "C:\\project",
  "grants": [
    {"path": "C:\\project", "mode": "write", "kind": "directory"},
    {"path": "C:\\external\\a.txt", "mode": "read", "kind": "file"}
  ],
  "worker_payload": {
    "call": {"id": "tool-call-id", "name": "read_file", "arguments": {}},
    "workspace": "C:\\project"
  }
}
```

`python_executable` 只指定当前受信 Python 运行时；Helper 固定追加 `-m mewcode.tool_worker`，协议不接受任意 executable arguments 或宿主命令。Helper 重新解析全部路径、验证文件系统类型和重解析点，并从实际目标重新推导 `kind`，不信任 Python 给出的分类。

Python 中使用冻结数据类构造协议：

```python
@dataclass(frozen=True)
class NativeGrant:
    path: str
    mode: AccessMode
    kind: Literal["file", "directory"]

@dataclass(frozen=True)
class WindowsRunRequest:
    protocol_version: int
    request_id: str
    timeout_ms: int
    python_executable: str
    workspace: str
    grants: tuple[NativeGrant, ...]
    worker_payload: dict[str, object]
```

### Rust → Python 运行响应

Helper stdout 只写一行 UTF-8 JSON，诊断日志写 stderr：

```json
{
  "protocol_version": 1,
  "request_id": "tool-call-id",
  "status": "completed",
  "worker_result": {
    "call_id": "tool-call-id",
    "name": "read_file",
    "ok": true,
    "output": "...",
    "summary": "...",
    "truncated": false,
    "error": null
  },
  "error": null
}
```

```python
class RunnerStatus(str, Enum):
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    FAILED = "failed"

@dataclass(frozen=True)
class RunnerError:
    code: str
    message: str

@dataclass(frozen=True)
class WindowsRunResponse:
    protocol_version: int
    request_id: str
    status: RunnerStatus
    worker_result: dict[str, object] | None
    error: RunnerError | None
```

`COMPLETED` 必须包含合法 `worker_result`；其他状态必须包含 `error`。Python 对响应做严格字段和类型校验，再复用统一的 `ToolResult` 反序列化器。协议版本不匹配、额外输出、截断 JSON 或 request ID 不一致均转换为 `sandbox_protocol_error`。

### Windows 准备状态

Rust Helper 的准备与诊断入口使用同一状态模型：

```rust
struct SandboxSetupState {
    schema_version: u32,
    install_id: String,
    owner_user_sid: String,
    appcontainer_profile_name: String,
    appcontainer_sid: String,
    helper_version: String,
    ready: bool,
}
```

`install_id` 由固定产品 ID 与当前宿主用户 SID 派生，使同一用户重复准备命中同一 AppContainer profile，不随工作区或应用小版本变化。状态位于 `%LOCALAPPDATA%\MewCode\sandbox\<install-id>\state.json`，只包含可公开诊断的标识、SID 与版本，不包含密码、令牌或可逆凭据；文件 ACL 仅允许当前宿主用户、Administrators 与 SYSTEM。准备操作必须幂等：profile 已存在且 SID 匹配时只更新状态，名称存在但 SID 或所有者异常时返回 `setup_failed`，不接管未知 profile。

### ACL 事务日志

```rust
enum GrantMode {
    Read,
    Write,
    Execute,
}

struct AclEntryRecord {
    canonical_path: String,
    file_id: String,
    sandbox_sid: String,
    rights: u32,
    inheritance: u32,
    ace_fingerprint: String,
}

struct AclTransactionJournal {
    schema_version: u32,
    transaction_id: String,
    owner_pid: u32,
    state: TransactionState,
    entries: Vec<AclEntryRecord>,
}
```

Helper 先以临时文件写日志并原子替换，再逐项添加 ACE；每成功一项立即更新日志。清理时校验路径文件 ID 和 ACE 指纹，只移除本事务创建的 ACE。路径已被替换、ACE 被外部修改或清理失败时不覆盖当前 DACL，记录 `acl_cleanup_failed` 并在下次诊断中报告人工恢复步骤。

权限映射固定为：`read` 仅文件/目录读取和元数据遍历；`execute` 增加执行但不增加写入；`write` 增加创建、修改、删除当前目标所需的最小权限。运行时根只读执行，工作区与临时目录采用既有 `write` grant，外部授权严格使用请求模式。

### 平台 Shell 规范

```python
@dataclass(frozen=True)
class ShellSpec:
    executable: str
    arguments_prefix: tuple[str, ...]
    encoding: str = "utf-8"

def current_shell_spec() -> ShellSpec: ...
```

Windows 固定选择系统 Windows PowerShell，参数前缀为 `-NoLogo -NoProfile -NonInteractive -Command`；macOS 固定选择 `/bin/sh -c`。`run_command` 以参数数组启动，不再设置 `shell=True`。Shell 路径缺失、编码初始化失败和命令行构造失败使用独立错误码。

### 稳定错误码

新增或细化以下沙箱错误码，均映射为现有 `ToolError`：

```text
unsupported_platform
component_missing
component_mismatch
component_integrity_failed
setup_required
setup_failed
appcontainer_profile_failed
appcontainer_policy_failed
path_not_supported
path_reparse_escape
acl_prepare_failed
acl_cleanup_failed
worker_start_error
sandbox_timeout
sandbox_cancelled
process_cleanup_failed
sandbox_protocol_error
```

Rust 错误内部可保留 Windows error code 供本地诊断，但返回模型和 TUI 的消息必须经过分类与脱敏，不包含访问令牌、完整环境或未请求的用户目录路径。

## 模块设计

### `mewcode/sandbox/base.py`、`models.py` 与 `__init__.py`

`base.py` 继续作为唯一平台后端选择入口，并为 `Sandbox` 增加 `diagnose()`。`SandboxFactory` 只按 `platform.system()` 创建后端，不在此处探测 PATH、安装组件或执行准备操作；Linux 与未知平台仍返回失败关闭后端。`models.py` 增加平台状态、诊断结果和原生授权数据结构，保持这些类型不依赖 Windows API，使 CLI、TUI 和单元测试可以跨平台导入。

`SandboxRequest` 仍是工具执行器与后端之间的唯一请求对象。工作区权限由 `PermissionStore` 统一加入，额外路径沿用一次/会话授权；Windows 协议对象只在进入 Windows 后端后由该请求派生，避免 Agent 或 Tool 层感知 AppContainer。

### `mewcode/sandbox/native_components.py`

新增平台原生组件解析器，负责定位、读取和校验 Windows Helper 清单。正式组件只从已安装 Python 包内的固定资源目录加载；源码开发态只接受仓库固定目录 `native/windows-sandbox-helper/target/release/`，且清单必须声明 `development=true`。解析器执行以下顺序：规范化候选路径、拒绝越界与重解析跳转、读取清单、校验应用/协议/架构、计算 SHA-256、检查 PE x86-64 标识，最后调用 Helper 的只读 `version` 操作核对自报版本。

该模块不搜索 PATH、不读取环境变量指定的覆盖路径，也不自动下载或构建二进制。所有失败都转换成 `SandboxDiagnostic` 或稳定组件错误码；原始路径只进入本地 debug 日志。

### `mewcode/sandbox/windows_protocol.py`

新增纯 Python 协议层，集中完成 `WindowsRunRequest` 的序列化与 `WindowsRunResponse` 的严格反序列化。协议层拒绝未知顶层字段、非整数超时、重复/空 request ID、相对路径、包含 NUL 的字符串、协议版本不匹配以及超过固定上限的请求或响应。它不启动进程、不修改权限，因此可在 macOS 和 CI 中完整单测。

Helper stdin/stdout 各自只承载一条 JSON 消息。stderr 仅用于经过脱敏的诊断；Python 不把 stderr 原样返回给模型。协议层只允许 Helper 启动当前已校验的 Python 解释器并固定执行 `-m mewcode.tool_worker`，不提供通用原生进程启动接口。

### `mewcode/sandbox/windows.py`

`WindowsSandbox` 负责编排组件解析、诊断和异步 Helper 生命周期。`diagnose()` 汇总 Windows 版本、NTFS 工作区能力、Helper 完整性、协议版本和 AppContainer profile 状态；`run()` 先把 `SandboxRequest` 转换成原生授权，再通过 `asyncio.create_subprocess_exec` 启动 Helper，将 JSON 写入 stdin 并关闭输入端。

超时或任务取消时，Python 先关闭 Helper stdin，再终止 Helper 并等待一个有界清理窗口；真正的工作进程树终止由 Helper 内的 Job Object 保证。Helper 非零退出、无响应、非法 JSON 或清理失败均返回结构化失败，不改由 Python 在宿主机重试执行。`WindowsSandbox` 不直接调用 `ctypes`，Windows 安全 API 全部留在 Rust 边界内。

### `mewcode/sandbox/macos.py` 与 `unavailable.py`

`MacOSSandbox` 补齐 `diagnose()`，并把现有 Seatbelt 启动、输出解析和取消处理对齐新的统一接口。现有 profile 规则继续作为 macOS 文件与进程边界，不因 Windows 改造扩大授权。`UnavailableSandbox.diagnose()` 返回 `UNSUPPORTED` 或 `BROKEN`，其 `run()` 继续明确拒绝执行。

### `mewcode/shell.py` 与 `mewcode/tools/command.py`

新增纯平台 Shell 适配模块。Windows 从系统目录解析 Windows PowerShell，不从当前目录或可变 PATH 选取同名程序，参数固定为 `-NoLogo -NoProfile -NonInteractive -Command`；macOS 固定为 `/bin/sh -c`。两端都以参数数组调用并约定 UTF-8 输出，不使用 `shell=True`。

`run_command` 只负责校验 `command`/`cwd`、调用平台 Shell、格式化 stdout/stderr 和退出码。Windows 不调用 `os.killpg`，macOS 的局部超时清理保留为防御措施；宿主沙箱 Runner 始终拥有最终超时和整棵进程树清理职责。为兼容 Windows PowerShell，工作进程环境显式设置 UTF-8 输入输出编码，并继续使用最小环境白名单。

### `mewcode/prompts.py` 与 `mewcode/agent.py`

`shell.py` 同时提供不含用户路径的只读平台上下文，例如 `Windows x86-64 / Windows PowerShell` 或 `macOS / POSIX sh`。`AgentLoop` 在每轮 system prompt 中追加该上下文，使模型生成符合当前平台的命令；Plan Mode 在相同平台上下文上叠加既有只读约束。平台上下文不改变 Provider、Conversation 或工具 schema，也不读取配置中的敏感字段。

测试断言普通模式与 Plan Mode 都只注入当前平台/Shell 一次，并验证 Windows 提示不会暗示 POSIX 命令、macOS 提示不会暗示 PowerShell。该信息只是命令生成上下文，不承担安全判定；最终安全边界仍由平台沙箱负责。

### `mewcode/cli.py` 与 TUI 状态展示

CLI 保持无参数时启动现有 TUI，并增加显式管理命令：`mewcode sandbox diagnose` 只读输出后端、状态、错误码和修复建议；`mewcode sandbox setup` 仅调用 Helper 创建/校验 AppContainer profile。setup 不由模型、Agent Loop 或 TUI 自动触发，也不在普通工具调用期间弹出 UAC。

TUI 启动时展示当前后端与简短状态；`SETUP_REQUIRED` 或 `BROKEN` 不阻止纯对话，但工具调用统一失败并给出可复制的诊断命令。界面不展示 AppContainer SID、完整本机路径或原始 Win32 错误文本。

### `native/windows-sandbox-helper/`

新增 Windows x86-64 Rust workspace，产出单一 `mewcode-windows-sandbox.exe`。内部模块按安全职责拆分：

- `protocol.rs`：严格解析 `version`、`diagnose`、`setup`、`run` 四种操作并输出稳定 JSON。
- `profile.rs`：用固定命名规则创建、查询和校验 AppContainer profile；拒绝接管身份不匹配的同名 profile。
- `paths.rs`：规范化 Windows 路径，验证卷类型、NTFS、文件 ID 和重解析点，推导运行时与授权根。
- `acl.rs`：为 AppContainer SID 建立、记录和回滚最小 ACE；恢复未完成事务时只移除带匹配指纹的本 Helper ACE。
- `appcontainer.rs`：构造无网络 capability 的 `SECURITY_CAPABILITIES` 和扩展启动属性；不使用实验 API。
- `job.rs`：创建 Job Object，设置 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`，并在恢复工作线程前把子进程加入 Job。
- `runner.rs`：以 suspended 状态创建 `tool_worker`，完成 Job 绑定后恢复；处理 stdin/stdout、超时、取消、进程退出及 ACL finally 清理。
- `state.rs` 与 `diagnostics.rs`：原子读写版本化状态/事务日志，生成脱敏诊断和稳定错误码。

`run` 的安全顺序固定为：校验协议与组件状态 → 规范化全部路径 → 计算并落盘 ACL 事务 → 添加 ACE → 创建 Job → 以 suspended AppContainer 进程创建 worker → 将进程加入 Job → 恢复进程 → 传输 worker JSON → 等待完成/超时/宿主断开 → 关闭 Job → 回滚 ACL。任一步失败都逆序清理；在进程加入 Job 之前不得恢复其执行。

AppContainer 不授予网络 capability，也不向工作进程转发宿主代理、凭据、token 或用户级配置环境变量。Helper 仅为经过验证的 Python 运行时目录添加只读/执行权限，为工作区和专用临时目录添加所需读写/执行权限，为用户批准的外部目标按 grant 添加最小权限；系统自身向 AppContainer 开放的基础资源视为 Windows 运行时边界的一部分。

### 构建、打包与测试模块

`pyproject.toml` 声明正式包内原生资源，但不增加运行期 Rust 依赖。新增固定 PowerShell 构建脚本执行 `cargo build --locked --release`、生成 manifest 和 SHA-256，并把产物复制到 Python 包的固定 Windows 资源目录；脚本只用于开发/发布流水线，不在用户启动时编译。macOS 安装包不携带 Windows 可执行文件，Windows 安装包携带匹配架构的 Helper；它们仍由同一源码版本和发布版本号产生，不是两套产品分支。

Python 单元测试覆盖平台选择、清单校验、协议解析、Shell 参数、诊断状态、超时与取消；Rust 单元测试覆盖协议、路径、状态和 ACL 指纹。Windows 集成测试在临时 NTFS 工作区验证读写边界、外部授权、默认断网、超时/取消后的子进程清理和崩溃后的 ACL 恢复；网络测试同时覆盖本地监听端口与外部地址，并验证主应用中的 mock Provider 请求不受影响。macOS 继续运行 Seatbelt smoke test；Linux CI 验证失败关闭。真实 AppContainer E2E 只在受支持的 Windows runner 执行，不能用 mock 结果替代安全验收。

Windows 最终验收还需在真实终端启动 MewCode，完成读文件、写文件、搜索、成功命令、非零退出命令、超时和正常退出的连续会话。平台无关交互可继续使用 Textual 测试驱动，但它不能替代真实 AppContainer/Job 路径；Windows 没有原生 tmux 时使用可审计的 Windows 终端/ConPTY 驱动并保存脱敏记录，macOS 继续按项目约定使用 tmux。

## 模块交互

### 启动与只读诊断

应用启动不执行安装、提权或 ACL 修改。`SandboxFactory` 创建平台后端后，TUI 调用 `diagnose()`：macOS 后端检查 Seatbelt 启动器；Windows 后端先让 `NativeComponentResolver` 校验 Helper，再向 Helper 发送 `diagnose`。Helper 只读核对操作系统版本、x86-64 架构、状态文件、AppContainer profile/SID、残留 ACL 事务和自身协议版本，并返回一个稳定状态。

```text
TUI/CLI
  │ diagnose()
  ▼
SandboxFactory → WindowsSandbox → NativeComponentResolver
                                      │ 校验 manifest/PE/SHA/version
                                      ▼
                              Windows Helper diagnose
                                      │
                     profile + state + journal 只读检查
                                      │
                                      ▼
                              SandboxDiagnostic
```

`READY` 允许工具执行；`SETUP_REQUIRED` 表示组件完整但尚未创建 profile；`BROKEN` 表示组件、profile、状态或残留事务异常；`UNSUPPORTED` 表示平台/版本/架构不在支持范围。诊断失败不影响纯文本对话，但每次工具调用都立即返回相同类别的失败结果，不尝试宿主执行。

### Windows 首次准备

用户显式执行 `mewcode sandbox setup`。Python 仍先完成 Helper 完整性校验，再发送 `setup`；Helper 根据产品 ID 与当前用户 SID 派生 profile 名称，查询现有 profile，并在缺失时创建 AppContainer profile。创建成功后重新查询 SID、原子写入版本化状态，再执行一次完整 diagnose，只有复检为 `READY` 才返回成功。

```text
用户 → CLI setup → 校验 Helper → Helper setup
                                   │
                                   ├─ 派生稳定 profile 名称
                                   ├─ 查询/创建 AppContainer profile
                                   ├─ 校验 profile SID 与当前用户状态
                                   ├─ 原子写 state.json
                                   └─ 重新 diagnose → READY
```

setup 必须可重复执行。已存在且匹配的 profile 不重复创建；同名但状态不匹配时失败关闭，不删除、不覆盖未知 profile。若某个 Windows 版本或企业策略要求管理员权限，CLI 必须在操作前明确说明并由用户主动确认，普通工具调用不得间接触发该流程。

### 正常工具调用

`ToolExecutor` 沿用现有工具登记和外部路径审批流程，生成平台无关的 `SandboxRequest`。`WindowsSandbox` 每次执行前重新诊断关键前置条件，把批准过的 grants 与 worker payload 序列化后启动 Helper。Helper 是该次调用的监督进程，不直接解释模型命令；模型命令只在受限的 `tool_worker` 内由对应工具处理。

```text
Agent → ToolExecutor → PermissionStore/用户审批
                            │ SandboxRequest
                            ▼
                     WindowsSandbox
                            │ 严格 JSON
                            ▼
                     Windows Helper
                            │
          ┌─────────────────┼──────────────────┐
          │ 路径/NTFS校验   │ ACL事务日志      │ AppContainer profile
          └─────────────────┼──────────────────┘
                            ▼
              Job Object + suspended worker
                            │ 加入 Job 后恢复
                            ▼
                   Python tool_worker
                            │
             files / search / platform shell
                            │ ToolResult JSON
                            ▼
Helper 关闭 Job、回滚 ACL → WindowsSandbox → ToolExecutor → Agent
```

Helper 启动 worker 时只传递协议所需 stdin 和最小环境；stdout 保留给唯一的 `ToolResult` JSON，stderr 由 Helper 捕获、限长并脱敏。Windows PowerShell 或其他子进程继承 AppContainer 令牌和 Job 归属，不能通过再创建子进程脱离文件、网络或生命周期边界。

并发只读工具各自使用独立 Helper、Job 和 ACL 事务。多个事务向同一路径添加的 ACE 必须具有不同事务指纹；任一调用结束只移除自己的 ACE，不影响仍在运行的调用。副作用工具继续由 `ToolExecutor` 串行执行。

### 外部路径授权

工作区 grant 由 `PermissionStore` 自动提供；工作区外目标必须先经过现有一次或会话授权。Python 规范化目标用于交互展示，Helper 再以句柄和文件 ID 独立校验实际目标。授权祖先目录时只开放路径遍历所需的最小元数据权限，不开放祖先目录内容枚举。

对于目录，Helper 在写入 ACL 与启动 worker 之间再次核对文件 ID 和重解析属性；对于文件，核对最终句柄对应的卷和文件 ID。若目标在审批后被替换、移动到非 NTFS 卷、变成重解析点或解析到授权边界外，调用返回 `path_reparse_escape`/`path_not_supported`，不扩大到新目标。

### 超时、取消与宿主退出

Python 和 Helper 使用同一 `timeout_ms`，但 Helper 是最终裁决者。Helper 计时到期时关闭 Job，使 worker 及全部子进程退出，等待进程句柄确认后回滚 ACL，并返回 `timed_out`。Python 的外层计时器略晚于 Helper，仅用于监督 Helper 卡死；不会在 Helper 仍清理时提前改为宿主执行。

Agent 取消导致 asyncio 任务取消时，`WindowsSandbox` 关闭 Helper stdin 并发送终止信号；Helper 把 stdin 断开或宿主终止视为取消，先关闭 Job、回滚 ACL，再退出。若 Helper 被强制杀死，Job 的 kill-on-close 保证工作进程树退出，事务日志保留给下一次恢复。macOS 后端也必须在取消时终止 Seatbelt 宿主进程并重新抛出 `CancelledError`。

### 崩溃恢复与清理失败

Helper 每次 `diagnose`、`setup` 或 `run` 开始前扫描未完成事务。恢复逻辑锁定事务日志，按记录重新打开目标、核对卷序列/文件 ID/ACE 指纹，只删除能证明属于该事务的 ACE；无法证明所有权时不修改 DACL，而把状态标记为 `BROKEN` 并返回人工修复提示。

运行完成但 ACL 回滚失败时，成功的 worker 结果不得掩盖安全清理失败：Helper 返回 `FAILED/acl_cleanup_failed`，本地诊断可记录被脱敏的目标标识。后续工具调用在残留事务清理成功前全部失败关闭。Job 关闭或子进程确认失败同理返回 `process_cleanup_failed`，不把调用报告为成功。

### 跨平台一致性边界

Agent、Provider、工具 schema、权限选择和 `ToolResult` 在 macOS 与 Windows 保持一致；差异仅位于宿主后端和命令语法。相同的抽象命令工具在 Windows 使用 PowerShell 语法，在 macOS 使用 POSIX Shell 语法，MewCode 不翻译用户命令，也不承诺一条平台专用命令可在另一平台原样运行。

发布时同一 MewCode 版本从同一代码库生成平台产物：macOS 包使用 Seatbelt 后端，Windows x86-64 包附带已校验 Helper。平台包是同一产品的构建变体，不维护独立功能分支；协议版本和 Python 应用版本不匹配时拒绝混用。

## 技术决策

### TD1：一个产品与主干，按平台生成构建产物

采用单一 Python 核心、统一版本号和统一功能主干，只把系统安全边界做成平台后端。macOS 与 Windows 可以拥有不同安装包和随包原生二进制，但它们不是两个产品版本，也不建立长期 `mac`/`windows` 功能分支。

该方式与 Codex、Claude Code 一类跨平台开发工具的通行结构一致：共享上层交互、协议和业务逻辑，在安装或发布阶段选择对应平台二进制与隔离能力。它避免需求、修复和工具 schema 在两个版本之间漂移，同时允许 Windows 使用 Windows 原生安全机制，而不是强行模拟 macOS Seatbelt。

### TD2：Windows 安全边界使用 Rust Helper，不在 Python 内直接封装 Win32

选择小型 Rust Helper 作为 Windows 原生信任边界。AppContainer、扩展进程启动属性、ACL、SID、Job Object 与 Windows 句柄生命周期需要精确的所有权和 finally 清理；Rust 的类型与资源析构更适合表达这些不变量，也便于把可审计的 unsafe Win32 调用收敛到少量模块。

不采用纯 Python `ctypes`/`pywin32` 作为主实现：`ctypes` 容易产生结构体布局、句柄与回调生命周期错误；引入 `pywin32` 仍不能替代原生 Runner 的进程监督，而且会把平台依赖扩散进 Python 核心。Helper 不是第二套业务实现，只接受固定协议并启动现有 `tool_worker`。

### TD3：使用 AppContainer，而不是专用账户、Restricted Token 或 WSL

Windows 原生隔离选择 AppContainer，理由是它提供默认拒绝的用户资源、凭据、网络、进程和窗口隔离，并允许传统 Win32 进程通过 `STARTUPINFOEX` 在该边界内启动。不给网络 capability 即保持默认断网；文件权限则通过 AppContainer SID 的显式 ACL grant 开放。

放弃“专用本地账户 + Restricted Token + 防火墙”的主要原因是：低权限账户仍可能读取公共 ACL 允许的宿主文件，而 Restricted Token 的双重访问检查若要兼顾 Python/PowerShell 运行时读取与严格工作区边界，需要大范围修改系统/运行时 ACL；同时还会引入密码保存、登录权利和防火墙规则生命周期，系统改动更大。

不把 WSL2 作为原生 Windows 的必需依赖。WSL2 可以作为未来额外后端，但要求用户安装并维护另一套 Linux 环境，且 Windows 路径、编辑器和进程体验不同，不能满足本阶段的原生 Windows 目标。

### TD4：不采用 `Experimental_CreateProcessInSandbox` 作为兼容基线

微软新提供的 `Experimental_CreateProcessInSandbox`/Bound File System API 能表达更直接的文件系统策略，但当前仍标记为实验能力，无法作为 Windows 10 1809+ 的稳定公共接口。当前 Helper 使用已有的 AppContainer profile、`SECURITY_CAPABILITIES`、ACL 与 Job Object API；将来只有在 API 稳定、支持矩阵明确并通过等价安全测试后，才可作为内部 Runner 优化，不能改变 Python 协议和用户权限语义。

### TD5：ACL 授权采用可恢复事务，不复制工作区

AppContainer 对普通用户资源默认不可见，因此按调用向 AppContainer SID 增加最小 ACE，并以文件 ID、继承范围和 ACE 指纹记录事务。调用结束只移除自己添加的 ACE；崩溃时由下次启动恢复。

不采用把整个项目复制进临时沙箱目录：大型仓库成本高，Git 状态、符号链接、构建缓存和编辑器协作会失真，也难以把修改安全同步回原工作区。不采用永久给 profile 授权整个用户目录或所有工作区，因为这会让一个会话的授权跨项目累积并破坏最小权限。

### TD6：Job Object 是 Windows 进程树生命周期的最终边界

每次调用一个 Job Object，并设置 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`。worker 必须以 suspended 状态创建，先加入 Job 再恢复，避免加入 Job 前产生逃逸子进程。Python 超时只是监督层；Helper 持有 Job 并负责最终超时、取消和子进程树终止。

不依赖 `taskkill`、父 PID 遍历或 Python 的 `os.killpg`：它们存在竞态、PID 复用或 Windows 不支持问题，也无法形成可靠的“Helper 消失即清理”语义。

### TD7：Shell 显式选择，不使用 `shell=True` 或跨平台命令翻译

Windows 固定调用系统 Windows PowerShell，macOS 固定调用 `/bin/sh`，都使用参数数组。选择系统 Windows PowerShell 是因为受支持 Windows 自带该组件，不要求用户额外安装 PowerShell 7；`-NoProfile -NonInteractive` 避免加载用户配置与交互提示。编码设置由适配层显式完成。

MewCode 不自动把 POSIX 命令改写为 PowerShell，也不提供用户可配置任意 Shell。命令翻译容易改变引用、管道和错误语义；任意 Shell 路径则扩大组件发现和执行面。平台差异通过提示、测试和明确错误呈现。

### TD8：Helper 协议严格、窄化且版本化

使用单请求/单响应 UTF-8 JSON，而不是长期驻留服务、命名管道 RPC 或通用命令执行协议。每次调用独立 Helper 可以把状态和 Job 生命周期绑定到一次工具调用，降低跨会话权限残留；JSON 便于 Python/Rust 双端测试和诊断。

协议只允许固定操作与固定 `tool_worker` 入口，不接受任意 executable/arguments、环境透传或网络 capability。未知字段、版本不匹配和额外 stdout 都失败关闭。后续扩展通过提高协议版本和兼容性检查完成，不静默猜测旧新字段语义。

### TD9：准备流程显式，运行流程永不自动提权

AppContainer profile 准备由 `mewcode sandbox setup` 显式触发，普通启动只诊断，工具调用只运行。即使某些系统策略要求管理员权限，也必须由用户在管理命令中主动确认；模型、Provider、Agent 和工具不能发起 UAC 或修改机器策略。

该决策保证纯对话始终可用，同时将系统状态变更与不可信模型输入隔离。诊断信息提供稳定错误码与修复命令，但不自动执行修复。

### TD10：原生组件随平台包发布并做完整性绑定

正式 Windows 包携带预编译 x86-64 Helper，manifest 将应用版本、协议版本、架构和 SHA-256 绑定；运行时不下载、不编译、不从 PATH 查找。源码开发允许固定 `target/release` 位置，但必须有开发清单并接受同等协议、架构与哈希校验。

该方式使 Windows 用户不需要安装 Rust。开发阶段当前机器尚无 Rust 工具链，因此真正进入 Helper 实现前需要单独安装受信的 Rust stable + MSVC 构建环境；安装属于开发环境变更，不由应用或测试脚本隐式完成。

### TD11：兼容性先通过真实 AppContainer 探针验证

AppContainer 的安全模型符合目标，但 Python 解释器、Windows PowerShell、依赖 DLL、证书/编码资源在不同 Windows 版本和企业策略下可能需要精确的只读/执行 grant。实施第一阶段先构建最小探针，验证受支持 Windows 上能够在无网络 capability 的 AppContainer 中启动固定 worker、读取运行时、执行 PowerShell 并由 Job 清理。

探针失败时不得退化为无沙箱执行。只能收窄或补充“必要系统/运行时资源”的明确授权，并为每项新增授权增加负向测试；若 Windows 10 1809+ 无法形成满足规格的稳定边界，则回到规格阶段重新评估支持基线，而不是在实现中隐藏例外。

## 文件组织

```text
MewCode_Agent/
├── mewcode/
│   ├── cli.py                              # 修改：TUI 默认入口与 sandbox 管理子命令
│   ├── prompts.py                          # 修改：注入当前平台与 Shell 上下文
│   ├── agent.py                            # 小改：普通/Plan Mode 复用平台上下文
│   ├── shell.py                            # 新增：Windows/macOS ShellSpec 与最小环境
│   ├── tool_worker.py                      # 小改：协议/编码错误保持单行 JSON
│   ├── tools/
│   │   └── command.py                      # 修改：移除 shell=True 与 POSIX 专用终止逻辑
│   ├── sandbox/
│   │   ├── __init__.py                     # 修改：导出诊断与协议公共类型
│   │   ├── base.py                         # 修改：diagnose 接口与平台工厂
│   │   ├── models.py                       # 修改：SandboxState/Diagnostic/原生授权模型
│   │   ├── macos.py                        # 修改：诊断、取消和协议错误对齐
│   │   ├── windows.py                      # 重写：Helper 编排、超时、取消、响应映射
│   │   ├── windows_protocol.py             # 新增：Python/Rust 严格 JSON 协议
│   │   ├── native_components.py            # 新增：manifest/PE/版本/SHA 校验
│   │   └── unavailable.py                  # 修改：统一诊断与失败关闭
│   ├── native/
│   │   └── windows-x86_64/                 # 发布构建生成，不接受运行时下载
│   │       ├── manifest.json
│   │       └── mewcode-windows-sandbox.exe
│   └── tui/
│       ├── app.py                          # 修改：启动诊断与后端状态
│       ├── screens.py                      # 修改：沙箱不可用提示
│       └── widgets.py                      # 按现有结构承载状态展示（需要时修改）
├── native/
│   └── windows-sandbox-helper/
│       ├── Cargo.toml
│       ├── Cargo.lock
│       ├── src/
│       │   ├── main.rs                     # CLI 分派与进程退出码
│       │   ├── protocol.rs                 # version/diagnose/setup/run 协议
│       │   ├── profile.rs                  # AppContainer profile 生命周期
│       │   ├── paths.rs                    # NTFS、句柄、文件 ID、重解析点
│       │   ├── acl.rs                      # ACE 事务与崩溃恢复
│       │   ├── appcontainer.rs             # SECURITY_CAPABILITIES 启动属性
│       │   ├── job.rs                      # Job Object RAII
│       │   ├── runner.rs                   # suspended worker 与监督流程
│       │   ├── state.rs                    # 原子状态/日志持久化
│       │   └── diagnostics.rs              # 稳定错误与脱敏
│       └── tests/
│           ├── protocol.rs
│           ├── path_policy.rs
│           └── appcontainer_integration.rs # Windows-only 真实安全边界测试
├── scripts/
│   └── build_windows_helper.ps1            # 新增：锁定构建、哈希与打包资源生成
├── tests/
│   ├── test_sandbox_backends.py            # 修改：统一 diagnose/factory 契约
│   ├── test_sandbox_windows.py             # 新增：Python Helper 生命周期与失败映射
│   ├── test_windows_protocol.py             # 新增：严格协议与恶意/截断输入
│   ├── test_native_components.py            # 新增：路径、PE、版本和完整性校验
│   ├── test_tools_command.py                # 修改：PowerShell/POSIX 参数与超时
│   ├── test_prompts.py                      # 新增：平台/Shell 上下文与敏感信息边界
│   ├── test_cli.py                          # 修改：diagnose/setup 命令
│   └── windows/
│       ├── test_appcontainer_e2e.py         # Windows runner：文件/网络/进程负向测试
│       └── test_acl_recovery_e2e.py         # Windows runner：崩溃与并发事务恢复
├── pyproject.toml                           # 修改：包资源、pytest marker、构建元数据
├── .gitignore                               # 修改：忽略 Rust target 与生成的发布资源
├── README.md                                # 新增：产品入口与平台支持矩阵
├── docs/
│   └── development.md                       # 新增：macOS/Windows 构建、测试、E2E 与修复
└── specs/04-cross-platform-runtime/
    ├── spec.md
    ├── plan.md
    ├── task.md
    └── checklist.md
```

`mewcode/native/windows-x86_64/` 是构建产物落点而不是手工维护源码。开发构建可在本地生成 manifest 与 exe，但 Git 默认忽略二进制；正式发布流水线从锁定的 Rust 源码构建并注入平台 wheel/安装包。`Cargo.lock` 必须提交，以保证 Helper 依赖可复现。

共享 Python 模块不得导入 Win32 FFI 包。除 `windows.py`、`windows_protocol.py` 和 `native_components.py` 外，平台判断只允许存在于 `SandboxFactory` 与 `shell.py`；Rust 源码不得复制工具实现、权限审批逻辑或 Provider 代码。

Windows E2E 使用 pytest marker 与操作系统/文件系统前置检查显式跳过不适用环境；安全单测和协议测试不能因缺少 Rust 工具链而被整体跳过。构建脚本生成 Helper 后，E2E 必须校验该产物的 manifest/SHA，并通过正式 `WindowsSandbox` 路径运行，避免测试一条与产品不同的直连路径。
