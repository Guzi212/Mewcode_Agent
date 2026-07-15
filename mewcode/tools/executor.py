"""工具顺序执行、外部路径审批与错误规范化。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from ..sandbox import (
    AccessGrant,
    AccessRequest,
    ApprovalScope,
    PermissionStore,
    Sandbox,
    SandboxRequest,
    resolve_target,
)
from .base import ToolSafety
from .models import (
    ToolCall,
    ToolExecutionEvent,
    ToolExecutionEventKind,
    ToolResult,
)
from .registry import ToolRegistry

ApprovalHandler = Callable[[AccessRequest], Awaitable[ApprovalScope]]


class ToolExecutor:
    """所有工具调用的唯一入口；失败不阻断同批后续调用。"""

    def __init__(
        self,
        registry: ToolRegistry,
        sandbox: Sandbox,
        workspace: str | Path,
        approve: ApprovalHandler | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._registry = registry
        self._sandbox = sandbox
        self._permissions = PermissionStore(workspace)
        self._approve = approve
        self._timeout = timeout
        self._approval_lock = asyncio.Lock()

    @property
    def permissions(self) -> PermissionStore:
        return self._permissions

    async def execute_batch(
        self,
        calls: list[ToolCall],
        cancel_requested: asyncio.Event | None = None,
    ) -> list[ToolResult]:
        """兼容旧调用方：收集过程事件并返回原序结果。"""
        results: list[ToolResult] = []
        async for event in self.execute_stream(calls, cancel_requested):
            if (
                event.kind == ToolExecutionEventKind.FINISHED
                and event.result is not None
            ):
                results.append(event.result)
        return results

    async def execute_stream(
        self,
        calls: list[ToolCall],
        cancel_requested: asyncio.Event | None = None,
    ) -> AsyncIterator[ToolExecutionEvent]:
        """连续只读并发、副作用串行，并按原始顺序报告结果。"""
        cancel_requested = cancel_requested or asyncio.Event()
        index = 0
        while index < len(calls):
            if cancel_requested.is_set():
                for call in calls[index:]:
                    yield ToolExecutionEvent.started(call)
                    yield ToolExecutionEvent.finished(call, self._cancelled(call))
                return

            call = calls[index]
            if self._registry.safety(call.name) == ToolSafety.READ_ONLY:
                end = index + 1
                while (
                    end < len(calls)
                    and self._registry.safety(calls[end].name) == ToolSafety.READ_ONLY
                ):
                    end += 1
                batch = calls[index:end]
            else:
                end = index + 1
                batch = [call]

            for item in batch:
                yield ToolExecutionEvent.started(item)
            results, was_cancelled = await self._execute_group(
                batch, cancel_requested
            )
            for item, result in zip(batch, results):
                yield ToolExecutionEvent.finished(item, result)

            index = end
            if was_cancelled:
                for item in calls[index:]:
                    yield ToolExecutionEvent.started(item)
                    yield ToolExecutionEvent.finished(item, self._cancelled(item))
                return

    async def _execute_group(
        self,
        calls: list[ToolCall],
        cancel_requested: asyncio.Event,
    ) -> tuple[list[ToolResult], bool]:
        tasks = [asyncio.create_task(self.execute(call)) for call in calls]
        cancel_task = asyncio.create_task(cancel_requested.wait())
        gather_task = asyncio.gather(*tasks, return_exceptions=True)
        done, _ = await asyncio.wait(
            {gather_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
        )
        was_cancelled = cancel_task in done and cancel_requested.is_set()
        if was_cancelled:
            for task in tasks:
                if not task.done():
                    task.cancel()
        if not gather_task.done():
            await gather_task
        if not cancel_task.done():
            cancel_task.cancel()
        await asyncio.gather(cancel_task, return_exceptions=True)

        results: list[ToolResult] = []
        for call, task in zip(calls, tasks):
            if task.cancelled():
                results.append(self._cancelled(call))
                continue
            try:
                value = task.result()
            except BaseException as exc:
                results.append(
                    ToolResult.failure(
                        call, "internal_error", f"工具执行失败：{exc}"
                    )
                )
            else:
                results.append(value)
        return results, was_cancelled

    @staticmethod
    def _cancelled(call: ToolCall) -> ToolResult:
        return ToolResult.failure(call, "cancelled", "工具调用已取消")

    async def execute(self, call: ToolCall) -> ToolResult:
        tool = self._registry.get(call.name)
        if tool is None:
            return ToolResult.failure(call, "unknown_tool", f"未登记的工具：{call.name}")
        try:
            request = tool.access_request(call)
        except ValueError as exc:
            return ToolResult.failure(call, "invalid_arguments", str(exc))

        once_grant: AccessGrant | None = None
        if request is not None:
            target = resolve_target(request.target, self._permissions.workspace)
            request = AccessRequest(
                request.tool_name,
                target,
                request.mode,
                request.command_summary,
            )
            if not self._permissions.is_allowed(target, request.mode):
                # 多个只读工具可以并发执行，但 TUI 审批弹窗必须逐个呈现。
                async with self._approval_lock:
                    if not self._permissions.is_allowed(target, request.mode):
                        if self._approve is None:
                            return ToolResult.failure(
                                call,
                                "permission_denied",
                                f"需要授权访问工作目录外路径：{target}",
                            )
                        scope = await self._approve(request)
                        if scope == ApprovalScope.DENY:
                            return ToolResult.failure(
                                call,
                                "permission_denied",
                                f"用户拒绝访问：{target}",
                            )
                        grant = AccessGrant(target, request.mode, scope)
                        if scope == ApprovalScope.SESSION:
                            self._permissions.add(grant)
                        else:
                            once_grant = grant

        sandbox_request = SandboxRequest(
            call,
            self._permissions.workspace,
            self._permissions.grants_for_call(once_grant),
        )
        try:
            return await tool.execute(
                call,
                lambda _: self._sandbox.run(sandbox_request, self._timeout),
            )
        except Exception as exc:  # 执行器边界：不让单个工具中断会话。
            return ToolResult.failure(call, "internal_error", f"工具执行失败：{exc}")
