"""MewCode 内置工具公共接口。"""

from __future__ import annotations

from typing import Any

__all__ = [
    "Tool",
    "ToolSafety",
    "build_default_registry",
    "ToolCall",
    "ToolDefinition",
    "ToolError",
    "ToolExecutionEvent",
    "ToolExecutionEventKind",
    "ToolExecutor",
    "ToolRegistry",
    "ToolResult",
]


def __getattr__(name: str) -> Any:
    """延迟导入，避免消息数据模型与工具抽象产生循环依赖。"""
    if name in {"Tool", "ToolSafety"}:
        from .base import Tool, ToolSafety

        return {"Tool": Tool, "ToolSafety": ToolSafety}[name]
    if name == "ToolRegistry":
        from .registry import ToolRegistry

        return ToolRegistry
    if name == "ToolExecutor":
        from .executor import ToolExecutor

        return ToolExecutor
    if name == "build_default_registry":
        from .registry import build_default_registry

        return build_default_registry
    if name in {
        "ToolCall",
        "ToolDefinition",
        "ToolError",
        "ToolExecutionEvent",
        "ToolExecutionEventKind",
        "ToolResult",
    }:
        from . import models

        return getattr(models, name)
    raise AttributeError(name)
