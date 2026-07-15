"""工具系统跨层共享的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class ToolDefinition:
    """提供给模型的协议无关工具定义。"""

    name: str
    description: str
    input_schema: dict[str, object]


@dataclass(frozen=True)
class ToolCall:
    """模型发起的一次完整工具调用。"""

    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class ToolError:
    """可安全回灌给模型的工具失败信息。"""

    code: str
    message: str


@dataclass(frozen=True)
class ToolResult:
    """工具调用的统一执行结果。"""

    call_id: str
    name: str
    ok: bool
    output: str
    summary: str
    truncated: bool = False
    error: ToolError | None = None

    @classmethod
    def failure(
        cls,
        call: ToolCall,
        code: str,
        message: str,
        *,
        summary: str | None = None,
        output: str = "",
    ) -> "ToolResult":
        """构造不抛异常的标准失败结果。"""
        return cls(
            call_id=call.id,
            name=call.name,
            ok=False,
            output=output,
            summary=summary or message,
            error=ToolError(code, message),
        )


class ToolExecutionEventKind(str, Enum):
    """批量调度过程中的工具事件。"""

    STARTED = "started"
    FINISHED = "finished"


@dataclass(frozen=True)
class ToolExecutionEvent:
    """一次工具调用开始或结束。"""

    kind: ToolExecutionEventKind
    call: ToolCall
    result: ToolResult | None = None

    @classmethod
    def started(cls, call: ToolCall) -> "ToolExecutionEvent":
        return cls(ToolExecutionEventKind.STARTED, call)

    @classmethod
    def finished(
        cls, call: ToolCall, result: ToolResult
    ) -> "ToolExecutionEvent":
        return cls(ToolExecutionEventKind.FINISHED, call, result)
