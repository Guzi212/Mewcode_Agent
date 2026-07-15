# 多协议 LLM 终端对话客户端 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为而非实现细节。
> 标注 `[真实API]` 的项需配置有效密钥、在 tmux 中真实运行验证。

## 实现完整性

- [ ] 配置能被加载与校验：多条 provider 的 YAML → `AppConfig`，`single()` 在单条时返回该项、多条时返回 `None`；缺 `api_key` 或 `protocol` 非法时抛 `ConfigError` 且不含密钥值（验证：`tests/test_config.py` 通过；对应 F1/AC1/N4/N5）
- [ ] 会话上下文正确组装：`build_context()` 首条为 SYSTEM(system prompt)，其后为交替历史（验证：`tests/test_conversation.py` 通过；对应 F4/F6）
- [ ] Provider 抽象不可直接实例化，两后端均实现统一 `stream`（验证：实例化 `Provider` 抛 `TypeError`）
- [ ] Anthropic 后端归一化正确且**丢弃思考增量**：喂含 thinking_delta 的 SSE，产出的事件里无任何思考文本，只得 text_delta / done（验证：`tests/test_anthropic.py` 通过；对应 F5/AC5）
- [ ] OpenAI 后端归一化正确：text_delta / done 依次产出（验证：`tests/test_openai.py` 通过；对应 F3）
- [ ] 工厂按 `protocol` 返回对应后端，非法 protocol 抛 `ProviderError`（验证：`tests/test_factory.py` 通过；对应 F3）
- [ ] Provider 选择屏可用：多条配置时 `ListView` 列出各项，回车返回选中的 `ProviderConfig`（验证：`tests/test_screens.py` 通过；对应 F2/AC2）

## 集成

- [ ] TUI 消费统一事件流并正确编排：text_delta 逐字显示 → DONE 后 markdown 重渲染定型、显示总耗时、assistant 进入 `conversation`、输入恢复可用（验证：`tests/test_app.py` 用 `run_test()` 注入假 Provider 通过；对应 F8/F12/AC8/AC12）
- [ ] 错误事件不中断应用：注入产出 `error` 的假 Provider，界面渲染 `ErrorMessage` 且应用不退出、输入恢复（验证：`tests/test_app.py` 通过；对应 F11/AC11）
- [ ] `/exit` 提交可退出应用（验证：`tests/test_app.py` 通过；对应 F10/AC10）
- [ ] 上层只依赖 `Provider` 接口与 `StreamEvent`，切后端不改 TUI/会话代码（验证：同一份 `test_app.py` 假 Provider 即证明解耦；对应 F3/N3）
- [ ] CLI 装配正确：`load_config` 抛 `ConfigError` 时 `main()` 返回非零且 stderr 可读；正常配置返回 0（验证：`tests/test_cli.py` 通过；对应 F1/AC1）

## 编译与测试

- [ ] 包可安装且可导入：`pip install -e ".[dev]"` 成功，`python -c "import mewcode; print(mewcode.__version__)"` 无错
- [ ] 全部单元/冒烟测试通过：`pytest` 全绿
- [ ] `python -m mewcode`（无配置文件时）打印可读错误并以非零码退出

## 端到端场景（tmux 真实运行）

- [ ] `[真实API]` 场景1（启动界面，AC7）：启动 `mewcode`，界面自上而下含 ASCII 猫咪 banner + `MewCode v版本` + 当前工作目录 + 就绪提示行 + 带 `❯` 与 "Send a message..." 占位的输入框 + 底部状态栏（左 provider 名、右 model）
- [ ] `[真实API]` 场景2（provider 选择，AC1/AC2）：仅一条配置时启动直接进入对话；配两条时启动出现方向键列表，选定后进入对话且状态栏显示所选 provider/model
- [ ] `[真实API]` 场景3（流式 + 计时，AC5/AC12/N2）：提问后，首个文本到达前即显示 `Imagining… (Ns)` 且秒数递增；随后回复逐字流式出现；本轮结束显示总耗时
- [ ] `[真实API]` 场景4（markdown 定型，AC8）：让模型输出含代码块/列表/强调的内容，回复结束后整段以 markdown 美化定型，格式正确渲染
- [ ] `[真实API]` 场景5（多轮记忆 + 不持久化，AC6）：第一轮告知「我的幸运数字是 7」，第二轮问「我的幸运数字是多少」答出 7；退出再启动，对话区为空（历史不保留）
- [ ] `[真实API]` 场景6（思考不显示，AC5/F5）：用会产生思考过程的配置提需推理的问题——DeepSeek 用 `deepseek-reasoner`（思考走 `reasoning_content`），或 Anthropic 用启用 `thinking` 的 Claude——界面全程不出现任何思考文本，仅显示最终回复（推理阶段界面静默、只有计时器在走，最终答案才逐字出现）
- [ ] `[真实API]` 场景7（切协议/自定义端点，AC3）：换用 openai 协议配置（含自定义 `base_url`）运行同一组对话，正常收发，交互体验与 Claude 一致
- [ ] 场景8（多行输入，AC9）：在输入框用 Alt+Enter 换行编辑多行，Enter 提交，提交后输入框清空；流式期间不接受新提交
- [ ] 场景9（错误可继续，AC11/N4）：故意用错误密钥或不存在的模型触发失败，错误在对话区以可区分样式显示，程序不退出，可继续下一轮对话
- [ ] 场景10（退出恢复终端，AC10/N7）：分别用 `/exit` 与 Ctrl+C 退出，退出后终端恢复正常（无残留 raw mode / 错乱）
- [ ] 场景11（界面不冻结，AC13/N1）：等待与流式期间，对话区可滚动、进行中指示可见，界面不冻结
- [ ] 场景12（配置健壮性，AC1/N4/N5）：删除或写坏配置文件、或去掉某条的 `api_key` 后启动，给出明确可读错误并退出，非崩溃堆栈，且错误信息不含密钥
