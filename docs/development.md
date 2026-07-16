# MewCode 跨平台开发指南

## 架构原则

MewCode 不为 Windows 复制一套产品代码。发布时仍使用同一个版本号和 Python 核心，但原生组件随目标平台打包：Windows wheel 包含已校验的 x86-64 Helper，macOS 不加载或查找该组件。

Windows 调用链如下：

```text
TUI / Agent / ToolExecutor
        │ 共享 ToolCall / ToolResult
        ▼
WindowsSandbox（Python，严格单行 JSON）
        ▼
Rust Helper
        ├─ 校验版本、PE 架构与 SHA-256
        ├─ 校验 NTFS 最终路径和文件 ID
        ├─ 临时添加最小 AppContainer ACL
        ├─ suspended 启动固定 Python worker
        ├─ 验证 worker token 的 AppContainer SID
        ├─ 加入 KILL_ON_JOB_CLOSE Job 后恢复执行
        └─ 结束后按事务精确回滚 ACL
```

Helper 不接受任意 executable、启动参数、环境变量或 capability。AppContainer capability 列表为空，工具默认断网；超时或 Helper 退出时由 Job Object 回收进程树。Windows PowerShell 在 AppContainer 中通过进程内临时 `MewCodeWorkspace:` PSDrive 定位到已授权工作区，避免授权盘符根目录。

## 开发前置

通用依赖：

- Python 3.10+
- Git

Windows 原生 Helper 还需要：

- Rust stable，目标 `x86_64-pc-windows-msvc`
- Visual Studio 2022 C++ Build Tools
- Windows 10/11 SDK
- 本地 NTFS 工作区

工具链可以安装到任意现有磁盘，不依赖 `C:` 或历史 `F:` 路径。若放在 D 盘，可在当前 PowerShell 会话中显式指定：

```powershell
$env:CARGO_HOME = 'D:\DevTools\Cargo'
$env:RUSTUP_HOME = 'D:\DevTools\Rustup'
$cargo = Join-Path $env:CARGO_HOME 'bin\cargo.exe'
& $cargo --version
```

不要把个人绝对路径提交到仓库；CI 或其他开发者可以让 `cargo` 位于 `PATH`。

## 安装 Python 依赖

仓库根目录执行：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

项目根据 `pyproject.toml` 使用 pip/setuptools，不需要 npm、pnpm 或 yarn。

## 构建 Windows Helper 和 wheel

`build_windows_helper.ps1` 总是执行 locked release 构建并生成包含版本、目标和 SHA-256 的 manifest。

兼容 Windows PowerShell 5.1 的 `.ps1` 源文件保持 ASCII；PowerShell 5.1 会把无 BOM 的 UTF-8 脚本按本地代码页解析，非 ASCII 注释可能意外形成反引号续行。需要中文时应写在 Markdown 文档中，或显式把脚本改为带 BOM 的 UTF-8 并同步调整自动化约束。

```powershell
# cargo 已在 PATH
.\scripts\build_windows_helper.ps1 -Package

# 或显式指定 D 盘 cargo，并构建 win_amd64 wheel
.\scripts\build_windows_helper.ps1 `
  -CargoPath 'D:\DevTools\Cargo\bin\cargo.exe' `
  -PythonPath '.\.venv\Scripts\python.exe' `
  -Offline `
  -Wheel
```

产物位置：

- Helper release：`native/windows-sandbox-helper/target/release/`
- wheel：`native/windows-sandbox-helper/target/wheel/`
- wheel 内原生资源：`mewcode/native/windows-x86_64/`

生成的 exe、manifest、wheel 和 Rust `target` 不提交 Git；`Cargo.lock` 应提交。

## 准备与诊断

```powershell
.\.venv\Scripts\mewcode.exe sandbox setup
.\.venv\Scripts\mewcode.exe sandbox diagnose
```

预期诊断：

```text
后端：windows-appcontainer
状态：ready
代码：ready
消息：Windows 原生沙箱已准备
```

常见状态：

| 状态/代码 | 含义 | 处理 |
|---|---|---|
| `setup_required` | profile 尚未显式准备 | 运行一次 `mewcode sandbox setup` |
| `component_missing` | Helper 或 manifest 缺失 | 重新构建/安装对应 Windows wheel |
| `component_integrity_error` | 架构、版本或 SHA 不匹配 | 恢复可信构建产物，不绕过校验 |
| `unsupported_*` | 系统、架构、卷或路径不受支持 | 改用支持的 x86-64 Windows 和本地 NTFS 工作区 |
| `sandbox_cleanup_failed` | ACL/进程清理无法安全完成 | 停止工具调用，保留事务状态并按诊断处理 |

企业策略若阻止 AppContainer profile 创建，应由管理员修复系统策略；不要增加无沙箱回退。

## 自动化验证

Python：

```powershell
.\.venv\Scripts\python.exe -m compileall -q mewcode tests scripts
.\.venv\Scripts\python.exe -m pytest -q -ra -p no:cacheprovider

# 真实启动 AppContainer、TUI、进程树和篡改夹具；basetemp 必须是专用本地 NTFS 目录
.\.venv\Scripts\python.exe -m pytest -q -ra -p no:cacheprovider `
  tests/windows --run-windows-native --basetemp '<专用 NTFS 测试目录>'
```

Rust：

```powershell
& $cargo fmt --manifest-path native/windows-sandbox-helper/Cargo.toml --check
& $cargo clippy --manifest-path native/windows-sandbox-helper/Cargo.toml --all-targets -- -D warnings
& $cargo test --manifest-path native/windows-sandbox-helper/Cargo.toml

# 会真实创建 AppContainer 进程，并在测试夹具上临时应用/回滚 ACL
& $cargo test --manifest-path native/windows-sandbox-helper/Cargo.toml -- --ignored --test-threads=1
```

重新构建后再运行组件解析测试，能同时验证 PE x86-64、协议/应用/Helper 版本和 SHA-256：

```powershell
.\scripts\build_windows_helper.ps1 -CargoPath $cargo -PythonPath '.\.venv\Scripts\python.exe' -Offline -Wheel
.\.venv\Scripts\python.exe -m pytest -q tests/test_native_components.py tests/test_sandbox_windows.py
```

## Windows 原生冒烟

冒烟测试必须使用专用 NTFS 测试目录和独立测试虚拟环境，不要指向包含用户文件的目录。先设置你自己的路径：

```powershell
$helper = '.\mewcode\native\windows-x86_64\mewcode-windows-sandbox.exe'
$python = '<专用测试虚拟环境>\Scripts\python.exe'
$workspace = '<专用 NTFS 测试工作区>'
$smoke = '.\scripts\smoke_windows_helper.py'
```

然后依次执行：

```powershell
foreach ($scenario in @(
  'diagnose', 'write', 'read', 'edit', 'search', 'find',
  'env', 'acl', 'nonzero', 'network', 'timeout'
)) {
  .\.venv\Scripts\python.exe $smoke `
    --helper $helper --python $python --workspace $workspace --scenario $scenario
  if ($LASTEXITCODE -ne 0) { throw "冒烟失败：$scenario" }
}
```

`outside` 场景还应传入专用工作区之外、确认不存在的绝对目标，并验证调用后仍不存在。`outside_read` 应指向专门创建且不含真实秘密的测试文件，期望读取失败且输出不含文件内容。测试结束后检查 AppContainer 事务目录为空、没有命令行包含 `-m mewcode.tool_worker` 的 Python 进程。

Windows 原生环境通常没有 tmux。本项目在 Windows 使用真实 Windows Terminal/ConPTY 会话完成 TUI E2E；macOS 仍按项目约定在 tmux 中验收。两边应使用同一配置，分别验证平台 Shell、文件工具、非零退出、超时、审批与正常退出。

## 干净副本复现

发布前可以用安全脚本把 Git 已跟踪和待纳入 Git 的源码复制到一个全新的目录；忽略的虚拟环境、用户配置、Rust target、Helper 和 wheel 不会被复制。目标目录必须不存在，脚本不会覆盖或删除既有目录：

```powershell
$env:CARGO_HOME = 'D:\DevTools\Cargo'
$env:RUSTUP_HOME = 'D:\DevTools\Rustup'

.\scripts\verify_windows_clean_room.ps1 `
  -Destination 'D:\DevTools\MewCodeCleanRoom' `
  -CargoPath 'D:\DevTools\Cargo\bin\cargo.exe' `
  -BootstrapPython '.\.venv\Scripts\python.exe' `
  -Wheelhouse 'D:\DevTools\MewCodeWheelhouse' `
  -OfflineCargo
```

`Wheelhouse` 可省略，此时 pip 会从配置的软件源安装 wheel 的运行依赖。`BootstrapPython` 可以指向现有 venv，脚本会解析其基础解释器后创建真正独立的新 venv。成功结果会输出 `CLEAN_ROOM_READY` 和 `CLEAN_ROOM_WHEEL`，并已依次完成 release Helper、`win_amd64` wheel、新虚拟环境、wheel 安装、setup、diagnose、完整 smoke 和工作区外读写阻断。

自动化完成后，真实 Provider、全新 Windows 用户和 macOS 实机仍需人工验收，步骤集中在 [人工验收清单](manual-acceptance.md)。

## 发布策略

跨平台适配不等于维护两个功能版本。推荐发布结构是：

- 一个仓库、一套功能规格和版本号；
- 平台无关的 Python 源码与测试；
- 少量平台后端；
- 按平台构建 wheel/安装包，Windows `win_amd64` wheel 携带 Rust Helper；
- CI 在 Windows 和 macOS runner 上分别执行原生安全 smoke。

这样能隔离系统 API 差异，同时避免 Windows/macOS 功能长期漂移。
