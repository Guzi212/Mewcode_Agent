"""MewCode 主应用：装配界面、编排一轮对话（计时 → 流式 → markdown 定型）。"""

from __future__ import annotations

import os
import time

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll

from .. import __version__
from ..agent import AgentLoop
from ..config import AppConfig
from ..conversation import Conversation
from ..messages import AgentEventKind
from ..prompts import SYSTEM_PROMPT
from ..providers import Provider, create_provider
from ..sandbox import AccessRequest, ApprovalScope, SandboxFactory
from ..tools.executor import ToolExecutor
from ..tools.registry import ToolRegistry, build_default_registry
from .screens import ProviderSelectScreen, ToolApprovalScreen
from .widgets import (
    AssistantMessage,
    Banner,
    ErrorMessage,
    PromptInput,
    ReadyLine,
    StatusBar,
    ThinkingIndicator,
    ToolMessage,
    UserMessage,
)


class MewCodeApp(App):
    # 覆盖 Textual 默认的 Ctrl+C 提示/复制行为，提供规格要求的明确退出方式。
    BINDINGS = [
        Binding("ctrl+c", "quit", show=False, priority=True),
        Binding("escape", "cancel_agent", show=False, priority=True),
    ]

    CSS = """
    Banner { height: auto; padding: 0 1; color: $accent; }
    ReadyLine { height: auto; padding: 0 1; }
    #conversation { height: 1fr; padding: 0 1; }
    .msg { margin: 0 0 1 0; }
    .msg.error { color: $error; }
    .thinking { color: $text-muted; margin: 0 0 1 0; }
    PromptInput { height: 5; border: round $accent; margin: 0 1; }
    StatusBar { height: 1; dock: bottom; background: $panel; padding: 0 1; }
    .status-right { width: 1fr; text-align: right; }
    """

    def __init__(
        self,
        config: AppConfig,
        provider: Provider | None = None,
        registry: ToolRegistry | None = None,
        executor: ToolExecutor | None = None,
        agent: AgentLoop | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._forced_provider = provider
        self._provider: Provider | None = None
        self._agent = agent
        self._busy = False
        self._elapsed = 0
        self._current_indicator: ThinkingIndicator | None = None
        self.conversation = (
            agent.conversation if agent is not None else Conversation(SYSTEM_PROMPT)
        )
        self._tool_registry = registry or build_default_registry()
        self._executor = executor or ToolExecutor(
            self._tool_registry,
            SandboxFactory.create(),
            os.getcwd(),
            approve=self._approve_external_path,
        )

    def compose(self) -> ComposeResult:
        yield Banner(__version__, os.getcwd())
        yield ReadyLine()
        yield VerticalScroll(id="conversation")
        yield PromptInput()
        yield StatusBar()

    def on_mount(self) -> None:
        if self._forced_provider is not None:
            self._activate(self._forced_provider)
            return
        single = self._config.single()
        if single is not None:
            self._activate(create_provider(single))
        else:
            self.push_screen(
                ProviderSelectScreen(self._config.providers), self._on_selected
            )

    def _on_selected(self, cfg) -> None:
        if cfg is None:
            self.exit()
            return
        self._activate(create_provider(cfg))

    def _activate(self, provider: Provider) -> None:
        self._provider = provider
        if self._agent is None:
            self._agent = AgentLoop(
                provider,
                self._tool_registry,
                self._executor,
                conversation=self.conversation,
                max_iterations=self._config.agent_max_iterations,
            )
        self.query_one(StatusBar).set_provider(provider.name, provider.model)
        self.query_one(PromptInput).focus()

    async def _approve_external_path(self, request: AccessRequest) -> ApprovalScope:
        """把执行器授权请求转换为 TUI modal 的用户选择。"""
        return await self.push_screen_wait(ToolApprovalScreen(request))

    def _tick(self, indicator: ThinkingIndicator) -> None:
        self._elapsed += 1
        indicator.show_elapsed(self._elapsed)

    def action_cancel_agent(self) -> None:
        if self._agent is not None and self._agent.cancel_current():
            if self._current_indicator is not None:
                self._current_indicator.show_cancelling()

    async def on_prompt_input_submitted(self, event: PromptInput.Submitted) -> None:
        text = event.text
        if text.strip() == "/exit":
            self.exit()
            return
        if self._provider is None or self._busy:
            return

        self._busy = True
        inp = self.query_one(PromptInput)
        inp.clear()
        inp.disabled = True

        self.run_worker(
            self._consume_agent(text),
            name="agent-loop",
            group="agent-loop",
            exclusive=True,
        )

    async def _consume_agent(self, text: str) -> None:
        area = self.query_one("#conversation", VerticalScroll)
        inp = self.query_one(PromptInput)
        indicator = ThinkingIndicator()
        assistant: AssistantMessage | None = None
        tool_lines: dict[str, ToolMessage] = {}
        self._current_indicator = indicator
        self._elapsed = 0
        start = time.monotonic()
        timer = self.set_interval(1, lambda: self._tick(indicator))

        async def finish_assistant() -> None:
            nonlocal assistant
            if assistant is not None:
                if assistant.text:
                    assistant.finalize()
                else:
                    await assistant.remove()
                assistant = None

        try:
            if self._agent is None:
                raise RuntimeError("Agent 尚未初始化")
            async for event in self._agent.run(text):
                if event.kind == AgentEventKind.USER_MESSAGE:
                    await area.mount(UserMessage(event.text))
                    await area.mount(indicator)
                elif event.kind == AgentEventKind.ITERATION_STARTED:
                    indicator.show_iteration(event.iteration, self._elapsed)
                elif event.kind == AgentEventKind.TEXT_DELTA:
                    if assistant is None:
                        assistant = AssistantMessage()
                        await area.mount(assistant)
                    assistant.append(event.text)
                elif event.kind == AgentEventKind.TOOL_STARTED and event.call is not None:
                    await finish_assistant()
                    line = ToolMessage(event.call.name, event.call.arguments)
                    tool_lines[event.call.id] = line
                    await area.mount(line)
                elif (
                    event.kind == AgentEventKind.TOOL_FINISHED
                    and event.call is not None
                    and event.result is not None
                ):
                    line = tool_lines.get(event.call.id)
                    if line is None:
                        line = ToolMessage(event.call.name, event.call.arguments)
                        tool_lines[event.call.id] = line
                        await area.mount(line)
                    line.complete(event.result.summary, event.result.ok)
                    if not event.result.ok and event.result.error is not None:
                        await area.mount(ErrorMessage(event.result.error.message))
                elif event.kind == AgentEventKind.USAGE_UPDATED and event.usage is not None:
                    self.query_one(StatusBar).set_usage(
                        event.usage.input_tokens, event.usage.output_tokens
                    )
                elif event.kind == AgentEventKind.ROUND_FINISHED:
                    await finish_assistant()
                elif event.kind == AgentEventKind.ERROR:
                    await area.mount(ErrorMessage(event.text))
                elif event.kind == AgentEventKind.TASK_FINISHED:
                    await finish_assistant()
                    break
                area.scroll_end()
        except Exception as exc:
            await finish_assistant()
            await area.mount(ErrorMessage(f"Agent 运行失败：{exc}"))
        finally:
            timer.stop()
            indicator.show_total(int(time.monotonic() - start))
            self._current_indicator = None
            inp.disabled = False
            inp.focus()
            self._busy = False
            area.scroll_end()
