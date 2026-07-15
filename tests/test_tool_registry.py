import pytest

from mewcode.sandbox.models import AccessRequest, SandboxRequest
from mewcode.tools import (
    Tool,
    ToolCall,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    ToolSafety,
)
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.registry import build_default_registry


class FakeTool(Tool):
    def __init__(self, name: str) -> None:
        self._definition = ToolDefinition(name, f"{name} 描述", {"type": "object"})

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def access_request(self, call: ToolCall) -> AccessRequest | None:
        return None

    async def execute(self, call: ToolCall, run_worker) -> ToolResult:
        return await run_worker(call)


class FakeSandbox:
    async def run(self, request: SandboxRequest, timeout: float) -> ToolResult:
        return ToolResult(request.call.id, request.call.name, True, "ok", "完成")


def test_registry_keeps_registration_order_and_finds_by_name():
    registry = ToolRegistry()
    registry.register(FakeTool("read_file"))
    registry.register(FakeTool("search_code"))

    assert [item.name for item in registry.definitions()] == ["read_file", "search_code"]
    assert registry.get("read_file") is not None
    assert registry.get("missing") is None


def test_registry_rejects_duplicate_name():
    registry = ToolRegistry()
    registry.register(FakeTool("read_file"))

    with pytest.raises(ValueError, match="重复"):
        registry.register(FakeTool("read_file"))


def test_default_registry_has_six_strict_definitions():
    registry = build_default_registry()
    definitions = registry.definitions()

    assert len(definitions) == 6
    assert {item.name for item in definitions} == {
        "read_file",
        "write_file",
        "edit_file",
        "run_command",
        "find_files",
        "search_code",
    }
    assert all(item.input_schema["additionalProperties"] is False for item in definitions)


def test_default_registry_exposes_only_read_only_definitions():
    registry = build_default_registry()

    assert [item.name for item in registry.definitions(read_only=True)] == [
        "read_file",
        "find_files",
        "search_code",
    ]
    assert registry.safety("read_file") == ToolSafety.READ_ONLY
    assert registry.safety("write_file") == ToolSafety.SIDE_EFFECT
    assert registry.safety("missing") is None


async def test_executor_keeps_running_after_unknown_tool(tmp_path):
    registry = ToolRegistry()
    registry.register(FakeTool("known"))
    executor = ToolExecutor(registry, FakeSandbox(), tmp_path)

    results = await executor.execute_batch(
        [ToolCall("1", "missing", {}), ToolCall("2", "known", {})]
    )

    assert [result.ok for result in results] == [False, True]
    assert results[0].error.code == "unknown_tool"
