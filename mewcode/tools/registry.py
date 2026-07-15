"""工具注册与发现。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from .base import Tool, ToolSafety
from .models import ToolCall, ToolDefinition, ToolResult
from ..sandbox.models import AccessMode, AccessRequest


class BuiltinTool(Tool):
    """由沙箱工作进程执行的固定工具声明。"""

    def __init__(
        self,
        definition: ToolDefinition,
        mode: AccessMode,
        path_key: str | None,
        safety: ToolSafety,
    ) -> None:
        self._definition = definition
        self._mode = mode
        self._path_key = path_key
        self._safety = safety

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    @property
    def safety(self) -> ToolSafety:
        return self._safety

    def access_request(self, call: ToolCall) -> AccessRequest | None:
        if self._path_key is None:
            return None
        raw = call.arguments.get(self._path_key, ".")
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"参数 {self._path_key} 必须是非空字符串")
        summary = call.arguments.get("command") if call.name == "run_command" else None
        return AccessRequest(call.name, Path(raw), self._mode, summary if isinstance(summary, str) else None)

    async def execute(
        self,
        call: ToolCall,
        run_worker: Callable[[ToolCall], Awaitable[ToolResult]],
    ) -> ToolResult:
        return await run_worker(call)


class ToolRegistry:
    """以登记顺序保存固定工具集。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        name = tool.definition.name
        if name in self._tools:
            raise ValueError(f"工具重复登记：{name}")
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(self, *, read_only: bool = False) -> list[ToolDefinition]:
        return [
            tool.definition
            for tool in self._tools.values()
            if not read_only or tool.safety == ToolSafety.READ_ONLY
        ]

    def safety(self, name: str) -> ToolSafety | None:
        tool = self.get(name)
        return None if tool is None else tool.safety

    def __len__(self) -> int:
        return len(self._tools)


def build_default_registry() -> ToolRegistry:
    """登记本阶段固定的六项核心工具。"""
    registry = ToolRegistry()
    definitions = [
        (
            ToolDefinition(
                "read_file", "读取 UTF-8 文本文件并返回带行号的内容。",
                {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
            ),
            AccessMode.READ,
            "path",
            ToolSafety.READ_ONLY,
        ),
        (
            ToolDefinition(
                "write_file", "创建或覆盖 UTF-8 文本文件，必要时创建父目录。",
                {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False},
            ),
            AccessMode.WRITE,
            "path",
            ToolSafety.SIDE_EFFECT,
        ),
        (
            ToolDefinition(
                "edit_file", "唯一匹配原文片段后替换文本；非唯一时不修改。",
                {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"], "additionalProperties": False},
            ),
            AccessMode.WRITE,
            "path",
            ToolSafety.SIDE_EFFECT,
        ),
        (
            ToolDefinition(
                "run_command", "在工作目录或经批准的目录执行 shell 命令。",
                {"type": "object", "properties": {"command": {"type": "string"}, "cwd": {"type": "string"}}, "required": ["command"], "additionalProperties": False},
            ),
            AccessMode.EXECUTE,
            "cwd",
            ToolSafety.SIDE_EFFECT,
        ),
        (
            ToolDefinition(
                "find_files", "按 glob 模式查找文件。",
                {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}}, "required": ["pattern"], "additionalProperties": False},
            ),
            AccessMode.READ,
            "path",
            ToolSafety.READ_ONLY,
        ),
        (
            ToolDefinition(
                "search_code", "在文本文件中搜索内容并返回文件、行号和命中行。",
                {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}}, "required": ["pattern"], "additionalProperties": False},
            ),
            AccessMode.READ,
            "path",
            ToolSafety.READ_ONLY,
        ),
    ]
    for definition, mode, path_key, safety in definitions:
        registry.register(BuiltinTool(definition, mode, path_key, safety))
    return registry
