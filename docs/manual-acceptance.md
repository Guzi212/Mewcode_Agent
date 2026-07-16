# MewCode 最终人工验收清单

自动化可完成的 Windows 构建、安全边界、进程清理、审批语义和 TUI 测试已经完成。以下项目需要真实账号、真实终端或另一台操作系统机器，请最后执行；不要把 API key、token 或用户文件内容放进验收记录。

## 1. Windows Terminal + 真实 Provider

在专用 D 盘测试工作区打开 Windows Terminal，使用用户已有的有效 Provider 配置：

```powershell
<MewCode虚拟环境>\Scripts\mewcode.exe sandbox diagnose
<MewCode虚拟环境>\Scripts\mewcode.exe
```

先确认 diagnose 显示 `windows-appcontainer / ready`，再依次让 Agent：

1. 完成一句不调用工具的普通对话。
2. 读取项目 `pyproject.toml`，在专用工作区写入带唯一标记的文本文件，再搜索该标记。
3. 执行输出中文和环境标记的 PowerShell 命令，再执行退出码 7 的命令和一个会超时的长命令。
4. 请求读取专门创建的工作区外测试文件：第一次在审批框选择“仅本次”，第二次相同请求选择“拒绝”，确认第二次没有沿用授权。
5. 再启动一个长命令并按 Escape，确认取消后普通对话仍成功；最后按 Ctrl+C 正常退出。

退出后再次运行 `sandbox diagnose`，应仍为 READY。只保存脱敏的工具名、摘要、错误码和测试文件标记，不保存 Provider 请求头、密钥或真实用户文件。

## 2. 全新 Windows 用户首次准备

使用新的普通本地用户或干净 Windows 10 1809+/11 x86-64 虚拟机；不要删除当前用户已经工作的 AppContainer profile 来模拟首次安装。

1. 把工作区和 venv 放在本地 NTFS 卷，安装同一个 `win_amd64` wheel。
2. 首次运行 `mewcode sandbox diagnose`，应显示 `SETUP_REQUIRED`；纯对话可用，工具失败关闭且提示显式 setup。
3. 手动运行一次 `mewcode sandbox setup`，再连续运行两次 setup，三次均不需要管理员日常权限且不会重复创建 profile。
4. 重新启动 TUI，确认 diagnose 为 READY，完成一次读文件和 PowerShell 命令后正常退出。

记录 diagnose 前后状态、组件版本和重复 setup 结果即可。

## 3. macOS 13+ Seatbelt/tmux

在 macOS 13+ 使用同一提交和同一配置安装依赖，然后执行：

```sh
python3 -m venv .venv
./.venv/bin/python -m pip install -e '.[dev]'
./.venv/bin/python -m pytest -q -ra
./.venv/bin/mewcode sandbox diagnose
tmux new-session -s mewcode-e2e './.venv/bin/mewcode'
```

diagnose 应显示 macOS Seatbelt 后端，且不要求 Windows Helper/setup。在 tmux 中依次验证纯对话、文件读写搜索、POSIX `$VAR`/管道/stdout/stderr/非零退出、外部路径“仅本次”和“拒绝”、网络被工具沙箱拒绝、长命令超时，以及 Ctrl+C 正常退出。

用 `tmux capture-pane -p -S -200` 保存脱敏结果。确认没有 Windows 专用提示、工具 schema 与审批选项和 Windows 版本一致，随后把 E1、E5、AC1、AC4、AC8、AC11 及最终完成条件的证据补回 checklist。
