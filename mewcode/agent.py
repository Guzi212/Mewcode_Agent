"""MewCode 自主 Agent Loop：循环、事件、停止条件与 Plan Mode。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum

from .conversation import Conversation
from .messages import (
    AgentEvent,
    StopReason,
    StreamEvent,
    StreamEventKind,
    TokenUsage,
)
from .prompts import EXECUTE_PLAN_PROMPT, PLAN_MODE_PROMPT, SYSTEM_PROMPT
from .providers import Provider
from .shell import platform_context
from .tools.base import ToolSafety
from .tools.executor import ToolExecutor
from .tools.models import (
    ToolCall,
    ToolExecutionEvent,
    ToolExecutionEventKind,
    ToolResult,
)
from .tools.registry import ToolRegistry


class AgentMode(str, Enum):
    EXECUTE = "execute"
    PLAN = "plan"


@dataclass(frozen=True)
class _ParsedInput:
    display_text: str
    task_text: str
    mode: AgentMode
    uses_saved_plan: bool = False
    error: str = ""


class AgentLoop:
    """持有单会话状态，并以异步事件流执行一次用户任务。"""

    def __init__(
        self,
        provider: Provider,
        registry: ToolRegistry,
        executor: ToolExecutor,
        *,
        conversation: Conversation | None = None,
        max_iterations: int = 20,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._executor = executor
        self.conversation = conversation or Conversation(SYSTEM_PROMPT)
        self._max_iterations = max_iterations if max_iterations > 0 else 20
        self._cancel_requested: asyncio.Event | None = None
        self._running = False
        self._pending_plan: str | None = None
        self._total_input = 0
        self._total_output = 0
        self._has_input_usage = False
        self._has_output_usage = False

    @property
    def pending_plan(self) -> str | None:
        return self._pending_plan

    @property
    def is_running(self) -> bool:
        return self._running

    def cancel_current(self) -> bool:
        if not self._running or self._cancel_requested is None:
            return False
        self._cancel_requested.set()
        return True

    async def run(self, text: str) -> AsyncIterator[AgentEvent]:
        """执行一次输入，直到自然完成或触发安全停止条件。"""
        if self._running:
            yield AgentEvent.error("已有 Agent 任务正在运行")
            yield AgentEvent.task_finished(StopReason.STREAM_ERROR, 0)
            return

        parsed = self._parse_input(text)
        yield AgentEvent.user_message(parsed.display_text)
        if parsed.error:
            yield AgentEvent.error(parsed.error)
            yield AgentEvent.task_finished(StopReason.STREAM_ERROR, 0)
            return

        self._running = True
        self._cancel_requested = asyncio.Event()
        try:
            self.conversation.add_user(parsed.task_text)
            system_prompt = f"{SYSTEM_PROMPT.rstrip()}\n\n{platform_context()}"
            if parsed.mode == AgentMode.PLAN:
                system_prompt = f"{system_prompt.rstrip()}\n\n{PLAN_MODE_PROMPT.strip()}"
            unknown_only_rounds = 0

            for iteration in range(1, self._max_iterations + 1):
                yield AgentEvent.iteration_started(iteration)
                text_parts: list[str] = []
                reasoning_parts: list[str] = []
                calls: list[ToolCall] = []
                round_usage = TokenUsage()
                stream_error = ""
                stream_cancelled = False
                tools = self._registry.definitions(
                    read_only=parsed.mode == AgentMode.PLAN
                )
                iterator = self._provider.stream(
                    self.conversation.build_history(system_prompt), tools
                ).__aiter__()

                try:
                    while True:
                        event, stream_cancelled, finished = await self._next_stream_event(
                            iterator
                        )
                        if stream_cancelled or finished:
                            break
                        if event is None:
                            continue
                        if event.kind == StreamEventKind.TEXT_DELTA:
                            text_parts.append(event.text)
                            yield AgentEvent.text_delta(event.text, iteration)
                        elif event.kind == StreamEventKind.REASONING_DELTA:
                            reasoning_parts.append(event.reasoning)
                        elif (
                            event.kind == StreamEventKind.TOOL_CALL
                            and event.call is not None
                        ):
                            calls.append(event.call)
                        elif (
                            event.kind == StreamEventKind.USAGE
                            and event.usage is not None
                        ):
                            round_usage = self._merge_usage(round_usage, event.usage)
                            yield AgentEvent.usage_updated(
                                self._usage_with_current_round(round_usage), iteration
                            )
                        elif event.kind == StreamEventKind.ERROR:
                            stream_error = event.text or "模型流式响应出错"
                            break
                        elif event.kind == StreamEventKind.DONE:
                            break
                finally:
                    await self._close_iterator(iterator)

                full_text = "".join(text_parts)
                full_reasoning = "".join(reasoning_parts)
                if full_text or calls:
                    self.conversation.add_assistant_turn(
                        full_text,
                        calls,
                        full_reasoning if calls else "",
                    )
                self._commit_usage(round_usage)

                if stream_cancelled or self._cancel_requested.is_set():
                    if calls:
                        async for agent_event in self._cancel_results(calls, iteration):
                            yield agent_event
                    yield AgentEvent.round_finished(iteration)
                    yield AgentEvent.error("当前任务已取消", iteration)
                    yield AgentEvent.task_finished(
                        StopReason.CANCELLED, iteration, "当前任务已取消"
                    )
                    return

                if stream_error:
                    if calls:
                        results = [
                            ToolResult.failure(call, "stream_error", stream_error)
                            for call in calls
                        ]
                        for call, result in zip(calls, results):
                            yield AgentEvent.tool_started(call, iteration)
                            yield AgentEvent.tool_finished(call, result, iteration)
                        self.conversation.add_tool_results(results)
                    yield AgentEvent.error(stream_error, iteration)
                    yield AgentEvent.round_finished(iteration)
                    yield AgentEvent.task_finished(
                        StopReason.STREAM_ERROR, iteration, stream_error
                    )
                    return

                if not calls:
                    if parsed.mode == AgentMode.PLAN:
                        self._pending_plan = full_text
                    yield AgentEvent.round_finished(iteration)
                    yield AgentEvent.task_finished(StopReason.COMPLETED, iteration)
                    return

                if all(self._registry.get(call.name) is None for call in calls):
                    unknown_only_rounds += 1
                else:
                    unknown_only_rounds = 0

                results: list[ToolResult] = []
                async for tool_event in self._execute_calls(
                    calls, parsed.mode, self._cancel_requested
                ):
                    if tool_event.kind == ToolExecutionEventKind.STARTED:
                        yield AgentEvent.tool_started(tool_event.call, iteration)
                    elif tool_event.result is not None:
                        results.append(tool_event.result)
                        yield AgentEvent.tool_finished(
                            tool_event.call, tool_event.result, iteration
                        )
                self.conversation.add_tool_results(results)
                yield AgentEvent.round_finished(iteration)

                if self._cancel_requested.is_set():
                    yield AgentEvent.error("当前任务已取消", iteration)
                    yield AgentEvent.task_finished(
                        StopReason.CANCELLED, iteration, "当前任务已取消"
                    )
                    return

                if unknown_only_rounds >= 3:
                    message = "连续 3 轮仅请求未知工具，任务已停止"
                    yield AgentEvent.error(message, iteration)
                    yield AgentEvent.task_finished(
                        StopReason.UNKNOWN_TOOL_LIMIT, iteration, message
                    )
                    return

            message = f"已达到 Agent 最大迭代次数 {self._max_iterations}"
            yield AgentEvent.error(message, self._max_iterations)
            yield AgentEvent.task_finished(
                StopReason.ITERATION_LIMIT, self._max_iterations, message
            )
        finally:
            self._running = False
            self._cancel_requested = None

    def _parse_input(self, text: str) -> _ParsedInput:
        stripped = text.strip()
        if stripped == "/plan" or stripped.startswith("/plan "):
            task = stripped[len("/plan") :].strip()
            if not task:
                return _ParsedInput(stripped, "", AgentMode.PLAN, error="/plan 需要任务描述")
            return _ParsedInput(stripped, task, AgentMode.PLAN)
        if stripped == "/do" or stripped.startswith("/do "):
            task = stripped[len("/do") :].strip()
            if task:
                return _ParsedInput(stripped, task, AgentMode.EXECUTE)
            if self._pending_plan is None:
                return _ParsedInput(
                    stripped,
                    "",
                    AgentMode.EXECUTE,
                    error="当前会话没有可执行的计划，请先使用 /plan <任务>",
                )
            return _ParsedInput(
                stripped,
                EXECUTE_PLAN_PROMPT,
                AgentMode.EXECUTE,
                uses_saved_plan=True,
            )
        if not stripped:
            return _ParsedInput(stripped, "", AgentMode.EXECUTE, error="任务不能为空")
        return _ParsedInput(stripped, stripped, AgentMode.EXECUTE)

    async def _next_stream_event(
        self, iterator: AsyncIterator[StreamEvent]
    ) -> tuple[StreamEvent | None, bool, bool]:
        assert self._cancel_requested is not None
        next_task = asyncio.create_task(anext(iterator))
        cancel_task = asyncio.create_task(self._cancel_requested.wait())
        done, _ = await asyncio.wait(
            {next_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if cancel_task in done and self._cancel_requested.is_set():
            next_task.cancel()
            await asyncio.gather(next_task, return_exceptions=True)
            return None, True, False
        cancel_task.cancel()
        await asyncio.gather(cancel_task, return_exceptions=True)
        try:
            return next_task.result(), False, False
        except StopAsyncIteration:
            return None, False, True
        except Exception as exc:
            return StreamEvent.error(f"Provider 流异常：{exc}"), False, False

    @staticmethod
    async def _close_iterator(iterator: AsyncIterator[StreamEvent]) -> None:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            try:
                await close()
            except RuntimeError:
                pass

    async def _execute_calls(
        self,
        calls: list[ToolCall],
        mode: AgentMode,
        cancel_requested: asyncio.Event,
    ) -> AsyncIterator[ToolExecutionEvent]:
        if mode != AgentMode.PLAN:
            async for event in self._executor.execute_stream(calls, cancel_requested):
                yield event
            return

        allowed: list[ToolCall] = []
        for call in calls:
            if self._registry.safety(call.name) == ToolSafety.SIDE_EFFECT:
                if allowed:
                    async for event in self._executor.execute_stream(
                        allowed, cancel_requested
                    ):
                        yield event
                    allowed = []
                result = ToolResult.failure(
                    call,
                    "tool_not_allowed_in_plan",
                    f"计划模式不允许执行工具：{call.name}",
                )
                yield ToolExecutionEvent.started(call)
                yield ToolExecutionEvent.finished(call, result)
            else:
                allowed.append(call)
        if allowed:
            async for event in self._executor.execute_stream(
                allowed, cancel_requested
            ):
                yield event

    async def _cancel_results(
        self, calls: list[ToolCall], iteration: int
    ) -> AsyncIterator[AgentEvent]:
        results: list[ToolResult] = []
        for call in calls:
            result = ToolResult.failure(call, "cancelled", "工具调用已取消")
            results.append(result)
            yield AgentEvent.tool_started(call, iteration)
            yield AgentEvent.tool_finished(call, result, iteration)
        self.conversation.add_tool_results(results)

    @staticmethod
    def _merge_usage(current: TokenUsage, latest: TokenUsage) -> TokenUsage:
        return TokenUsage(
            latest.input_tokens
            if latest.input_tokens is not None
            else current.input_tokens,
            latest.output_tokens
            if latest.output_tokens is not None
            else current.output_tokens,
        )

    def _usage_with_current_round(self, current: TokenUsage) -> TokenUsage:
        return TokenUsage(
            self._total_input + current.input_tokens
            if current.input_tokens is not None
            else (self._total_input if self._has_input_usage else None),
            self._total_output + current.output_tokens
            if current.output_tokens is not None
            else (self._total_output if self._has_output_usage else None),
        )

    def _commit_usage(self, usage: TokenUsage) -> None:
        if usage.input_tokens is not None:
            self._total_input += usage.input_tokens
            self._has_input_usage = True
        if usage.output_tokens is not None:
            self._total_output += usage.output_tokens
            self._has_output_usage = True
