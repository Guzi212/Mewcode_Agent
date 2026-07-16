# MewCode

MewCode 是一个用 Python 实现的终端 AI 编程助手。项目采用**同一产品、同一代码库、按平台选择原生沙箱后端**的结构，不维护独立的 Windows 代码分支：

- macOS：Seatbelt（`sandbox-exec`）+ POSIX `sh`
- Windows：Rust Helper + AppContainer + Job Object + 临时 NTFS ACL + Windows PowerShell
- Linux / WSL：当前明确不支持工具执行，后端失败关闭；纯对话能力不因此自动获得宿主执行权限

Windows 和 macOS 共享 Provider、Agent Loop、工具 schema、审批语义、TUI 和 Python 工具实现。只有系统隔离、进程管理、路径规则和 Shell 入口位于平台边界。

## 支持范围

| 平台 | 状态 | 原生执行后端 | 命令 Shell |
|---|---|---|---|
| macOS 13+ | 支持 | Seatbelt | `/bin/sh` |
| Windows 10 1809+ / Windows 11 x86-64 | 支持 | AppContainer + Job Object + NTFS ACL | Windows PowerShell 5.1 |
| Linux / WSL | 暂不支持工具执行 | 失败关闭 | 不启用 |

Windows 工作区必须位于本地 NTFS 卷。目前不支持 Windows ARM64、网络盘、映射盘或非 NTFS 工作区。

## 快速开始

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 首次准备 Windows AppContainer；重复执行是幂等的
.\.venv\Scripts\mewcode.exe sandbox setup
.\.venv\Scripts\mewcode.exe sandbox diagnose

# 启动 TUI
.\.venv\Scripts\mewcode.exe
```

常规使用不需要管理员权限。`sandbox setup` 只能由用户显式执行，Agent、模型和 TUI 不会自动创建 AppContainer profile。

Windows Helper 构建、平台 wheel、测试和真实冒烟流程见 [开发文档](docs/development.md)。跨平台实现规格与验收状态见 [Windows 适配 checklist](specs/04-cross-platform-runtime/checklist.md) 和 [验收报告](specs/04-cross-platform-runtime/acceptance-report.md)；只需人工完成的项目见 [人工验收清单](docs/manual-acceptance.md)。
