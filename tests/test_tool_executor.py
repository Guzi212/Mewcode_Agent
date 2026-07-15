import asyncio

from mewcode.sandbox.models import AccessRequest, SandboxRequest
from mewcode.tools import (
    Tool,
    ToolCall,
    ToolDefinition,
    ToolExecutionEventKind,
    ToolRegistry,
    ToolResult,
    ToolSafety,
)
from mewcode.tools.executor import ToolExecutor


class ClassifiedTool(Tool):
    def __init__(self, name: str, safety: ToolSafety) -> None:
        self._definition = ToolDefinition(name, name, {"type": "object"})
        self._safety = safety

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    @property
    def safety(self) -> ToolSafety:
        return self._safety

    def access_request(self, call: ToolCall) -> AccessRequest | None:
        return None

    async def execute(self, call: ToolCall, run_worker) -> ToolResult:
        return await run_worker(call)


class RecordingSandbox:
    def __init__(self, delays: dict[str, float]) -> None:
        self.delays = delays
        self.timeline: list[str] = []
        self.active_readers = 0
        self.max_readers = 0
        self.active_effects = 0
        self.max_effects = 0

    async def run(self, request: SandboxRequest, timeout: float) -> ToolResult:
        call = request.call
        is_read = call.name.startswith("read")
        self.timeline.append(f"start:{call.id}")
        if is_read:
            self.active_readers += 1
            self.max_readers = max(self.max_readers, self.active_readers)
        else:
            self.active_effects += 1
            self.max_effects = max(self.max_effects, self.active_effects)
        await asyncio.sleep(self.delays.get(call.id, 0))
        if is_read:
            self.active_readers -= 1
        else:
            self.active_effects -= 1
        self.timeline.append(f"end:{call.id}")
        return ToolResult(call.id, call.name, True, call.id, f"完成 {call.id}")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ClassifiedTool("read_a", ToolSafety.READ_ONLY))
    registry.register(ClassifiedTool("read_b", ToolSafety.READ_ONLY))
    registry.register(ClassifiedTool("write", ToolSafety.SIDE_EFFECT))
    return registry


async def test_read_batches_are_concurrent_and_results_keep_call_order(tmp_path):
    sandbox = RecordingSandbox({"1": 0.03, "2": 0.01})
    executor = ToolExecutor(_registry(), sandbox, tmp_path)
    calls = [
        ToolCall("1", "read_a", {}),
        ToolCall("2", "read_b", {}),
        ToolCall("3", "write", {}),
        ToolCall("4", "read_a", {}),
    ]

    events = [event async for event in executor.execute_stream(calls)]
    finished = [
        event.result.call_id
        for event in events
        if event.kind == ToolExecutionEventKind.FINISHED and event.result is not None
    ]

    assert sandbox.max_readers == 2
    assert sandbox.max_effects == 1
    assert finished == ["1", "2", "3", "4"]
    assert sandbox.timeline.index("start:3") > sandbox.timeline.index("end:1")
    assert sandbox.timeline.index("start:4") > sandbox.timeline.index("end:3")


async def test_unknown_tool_does_not_block_later_call(tmp_path):
    executor = ToolExecutor(_registry(), RecordingSandbox({}), tmp_path)
    calls = [ToolCall("1", "missing", {}), ToolCall("2", "read_a", {})]

    results = await executor.execute_batch(calls)

    assert [result.ok for result in results] == [False, True]
    assert results[0].error is not None
    assert results[0].error.code == "unknown_tool"


class BlockingSandbox:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def run(self, request: SandboxRequest, timeout: float) -> ToolResult:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("取消后不应继续")


async def test_cancel_finishes_inflight_and_unstarted_calls(tmp_path):
    sandbox = BlockingSandbox()
    executor = ToolExecutor(_registry(), sandbox, tmp_path)
    cancel_requested = asyncio.Event()
    calls = [
        ToolCall("1", "read_a", {}),
        ToolCall("2", "read_b", {}),
        ToolCall("3", "write", {}),
    ]

    async def collect():
        return [
            event
            async for event in executor.execute_stream(calls, cancel_requested)
        ]

    task = asyncio.create_task(collect())
    await sandbox.started.wait()
    cancel_requested.set()
    events = await asyncio.wait_for(task, timeout=1)
    results = [
        event.result
        for event in events
        if event.kind == ToolExecutionEventKind.FINISHED
    ]

    assert [result.call_id for result in results if result] == ["1", "2", "3"]
    assert all(result and result.error and result.error.code == "cancelled" for result in results)


async def test_already_cancelled_batch_returns_cancelled_results(tmp_path):
    executor = ToolExecutor(_registry(), RecordingSandbox({}), tmp_path)
    cancel_requested = asyncio.Event()
    cancel_requested.set()
    calls = [ToolCall("1", "write", {}), ToolCall("2", "read_a", {})]

    results = await executor.execute_batch(calls, cancel_requested)

    assert [result.call_id for result in results] == ["1", "2"]
    assert all(result.error and result.error.code == "cancelled" for result in results)
