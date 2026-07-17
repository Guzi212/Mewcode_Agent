# 系统提示工程化验收报告

## 结论

实现、自动化回归、本地 tmux 工具闭环、真实 DeepSeek 缓存 smoke 和同模型五场景人工 A/B 已全部完成。模块化提示、动态系统补充项、Plan Mode 轮次策略、Anthropic 显式缓存映射、OpenAI-compatible 稳定前缀、缓存用量解析与工具规则双重强化均有实际证据。

真实 smoke 第二轮返回 `cache_read=1408`，证明稳定前缀缓存生效。人工 A/B 结果为混合结论：新提示在失败场景更直接、Plan 约束说明更清楚，但代码定位场景旧/新提示均错误调用 `run_command` 并达到 6 轮上限，因此不能宣称新提示在所有场景全面优于旧提示。

## 自动化证据

- 开发前基线：`154 passed, 18 skipped`。
- 最终全量：`178 passed, 18 skipped`，无失败；18 个 skip 均为既有 Windows 平台或显式原生测试条件。
- 聚焦测试：Prompt、环境、Conversation、Anthropic、OpenAI、Agent、工具注册、TUI、smoke 和 macOS 沙箱回归全部通过。
- Python 编译：`.venv/bin/python -m compileall -q mewcode tests scripts` 退出 0。
- Smoke CLI：`.venv/bin/python scripts/smoke_prompt_cache.py --help` 退出 0。
- Diff 检查：`git diff --check` 退出 0。
- Ruff 与 mypy：项目未配置、虚拟环境未安装，本章没有新增依赖，也未伪报通过。

自动测试覆盖：

- 七个固定模块顺序、同优先级稳定排序、空槽跳过和逐字节确定性。
- 环境字段、非 Git、Git 缺失、Git 超时、平台读取失败、事件循环线程边界和敏感值隔离。
- typed `SYSTEM_REMINDER` 的临时注入、用户同名标签不提权、工具调用与结果配对。
- Anthropic 稳定 system/最后工具 `cache_control`、动态块无缓存标记、创建/读取用量与总输入归一化。
- OpenAI-compatible system 消息顺序、无显式缓存扩展、标准 cached token、可选写入与 DeepSeek 命中字段。
- 缓存字段缺失、布尔、负数和非法类型保持未知；明确 0 与未知区分。
- 同轮多个用量快照和多轮累计只计每轮最终值一次。
- Plan Mode 第 1、6、11、16 轮完整提醒，其余精简；新任务重新从完整提醒开始。
- Plan Mode 工具列表只读，执行层仍拒绝副作用工具；TUI 状态栏继续只显示输入/输出 Token。
- Anthropic 与 OpenAI-compatible 获得等价的稳定系统正文、动态 reminder 和用户历史语义。

## tmux 端到端证据

验收使用本地 OpenAI-compatible 脚本 Provider、公开固定文本临时夹具和真实 MewCode TUI/Agent Loop/沙箱工具执行链，不连接外部网络。临时会话和临时目录已在验收后移除；用户原有 tmux 会话未被终止。

### 普通模式精确修改

可观察工具顺序：

```text
find_files → read_file → edit_file → read_file → 最终答复
```

四个工具均成功，精确替换一处文本，随后读取验证。TUI 状态栏只显示累计输入/输出 Token，没有缓存明细。

### Plan Mode 与 `/do`

`/plan` 面对“直接修改，不要只给计划”的请求时，只执行：

```text
read_file → 输出计划
```

读取后确认临时文件仍保持原值，没有副作用工具。输入 `/do` 后执行：

```text
edit_file → read_file → 最终答复
```

精确修改成功并读取验证，证明执行模式工具恢复、工具调用与结果配对合法。

### 失败事实性与恢复

读取明确不存在的临时文件时，`read_file` 返回失败，最终答复明确表示无法提供第一行，没有编造内容。随后同一会话再次完成 `find_files → read_file`，证明工具失败后会话可恢复。

### 验收发现并修复的缺陷

首次真实 macOS Seatbelt 工具执行发现宿主向严格 JSONL worker 发送的请求缺少末尾换行，worker 因而统一返回 `invalid_request`。已在 `mewcode/sandbox/macos.py` 补充单个换行，并增加回归测试断言请求恰为一行 JSONL。修复后上述 tmux 工具闭环全部成功。

## 真实缓存 smoke 状态

用户知情授权后，使用 `deepseek-v4-flash` 连续发送两轮相同稳定前缀：

| 轮次 | input | output | cache_read | cache_write | 首字等待 |
|---|---:|---:|---:|---|---:|
| 1 | 1,501 | 33 | 0 | `unknown` | 1,094.2 ms |
| 2 | 1,527 | 32 | 1,408 | `unknown` | 1,183.1 ms |

第二轮真实命中 1,408 Token。端点没有返回缓存写入字段，统一用量正确保持 `unknown`，没有伪造成 0。

## 真实模型 A/B

旧提示与新提示均使用 `deepseek-v4-flash`、未显式配置思考、同一 6 轮上限、同一工具集、相同固定输入和独立等价工作区：

- 场景 1：双方都优先使用 `search_code`，随后都违规调用 `run_command` 并达到迭代上限；新提示没有改善该场景。
- 场景 2：双方都先读取、精确修改并读取验证；新提示额外调用一次 `find_files`。
- 场景 3：双方都只读调查且未修改夹具；新提示对系统约束和模式切换说明更明确。
- 场景 4：旧提示 `find_files → read_file`，新提示直接 `read_file`；双方都如实报告失败，新提示更直接且首字等待更短。
- 场景 5：双方均按 `find_files → read_file → edit_file → read_file` 完成。
- 10 个真实场景均未复述或针对 `<system-reminder>` 标签本身作答。

完整工具序列、缓存读取和首字等待见 `manual-evaluation.md`。本评估不自动打分，也不把混合结果包装成单向提升。

## 未完成项

无。Spec AC1–AC17 均已有自动化、协议捕获、真实 tmux 或真实 Provider 证据。

## 工作区说明

开发前已存在且未触碰的工作区项包括 `.DS_Store` 和 `.agents/skills/mew-spec/SKILL.md` 的用户改动。未执行 commit、push、rebase 或 PR。
