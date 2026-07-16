# MewCode 跨平台执行与 Windows 原生支持验收报告

日期：2026-07-16

## 结论

Windows 适配的实现、原生安全边界、构建、安装和自动化 TUI 验证已完成，当前 Windows 环境未发现阻断开发的问题。产品采用同一仓库和共享核心，运行时按平台选择 Seatbelt 或 AppContainer，不维护独立 Windows 功能版本。

整体跨平台发布验收尚未最终关闭：还缺 macOS 13+ 实机 Seatbelt/tmux、用户真实 Provider 的 Windows Terminal 会话，以及全新 Windows 用户的首次 setup 观察。对应步骤见 `docs/manual-acceptance.md`。

## 自动化结果

| 范围 | 实际结果 |
|---|---|
| Python 编译 | `python -m compileall -q mewcode tests scripts`，退出码 0 |
| Python 默认回归 | 141 passed、10 skipped、0 failed；8 个 skip 是需显式启用的 Windows 原生测试 |
| Windows 原生 Python/TUI E2E | 8 passed、0 failed，46.47s |
| Rust 格式与静态检查 | `cargo fmt --check`、`cargo clippy --all-targets -- -D warnings`，退出码 0 |
| Rust 普通测试 | 全部适用测试通过，原生测试明确 ignored |
| Rust 真实原生测试 | 14 passed、0 failed，单线程执行 |
| 最终 Windows wheel | `mewcode-0.1.0-py3-none-win_amd64.whl`；SHA-256 `edcb1ff45b625d23e9b64b02265bf18bd9ce50dcb8ddff19a7771b1a10679d8f` |
| D 盘安装验证 | 强制重装到独立 D 盘 venv 后，diagnose 为 `windows-appcontainer / ready / 0.1.0` |
| D 盘干净副本 | 从未包含 venv/target/Helper/wheel 的全新源码副本离线构建、安装、setup、diagnose 和完整 smoke，输出 `CLEAN_ROOM_READY` |

默认回归的其余两个 skip 分别是 macOS Seatbelt 实机路径和当前 Windows 会话缺少 symlink 创建权限；通用 NTFS reparse 拒绝逻辑、真实 junction 和审批后目标替换已由 Rust 原生测试覆盖。

## 已观察的 Windows 原生行为

- 六类工具均经固定 AppContainer worker 执行；工作区内读写改查成功，未授权外部读写失败。
- worker token 的 AppContainer SID 与 profile 匹配，capability 为空，本地 TCP 监听端未收到连接，敏感环境变量未进入 worker。
- PowerShell stdout、stderr、中文、非零退出和超时均转换为稳定结构化结果。
- 三层进程树超时、Agent 取消和宿主 Python 强退后，Job 中后代消失、ACL 事务清零、diagnose 恢复 READY，下一次命令成功。
- 单文件只读授权不扩展到同目录其他文件，不能写回目标；一次授权不会自动延续到下一次请求。
- 内容篡改、PE 架构伪造和版本错配均在执行前被拒绝；恢复正确组件后无需改配置即可 READY。
- Textual 真实事件循环连接实际 WindowsSandbox，完成工具、审批、拒绝、超时、Escape 取消、Ctrl+C 和后续对话恢复。

## 剩余风险

- macOS 代码契约和共享测试已保留，但本次 Windows 主机不能提供真实 Seatbelt 证据。
- 自动化 TUI 使用脚本 Provider，不能代替用户真实模型账号、真实网络 Provider 和 Windows Terminal/ConPTY 的最终体验观察。
- 当前 Windows 用户的 AppContainer profile 已准备完成；不应为模拟首次安装而破坏性删除它，首次 setup 需在新用户或干净虚拟机观察。

详细条目状态和证据映射见 `checklist.md`。
