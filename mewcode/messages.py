"""跨层共享的数据结构：会话历史与统一流式事件。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .tools.models import ToolCall, ToolResult


class Role(str, Enum):
    """对话消息角色。"""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class Message:
    """一条对话消息（本阶段仅纯文本）。"""

    role: Role
    content: str


class ConversationItemKind(str, Enum):
    """协议无关的会话历史项类型。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_CALLS = "tool_calls"
    TOOL_RESULTS = "tool_results"


@dataclass(frozen=True)
class ConversationItem:
    """可同时表达文本、工具调用和工具结果的历史项。"""

    kind: ConversationItemKind
    text: str = ""
    calls: tuple[ToolCall, ...] = ()
    results: tuple[ToolResult, ...] = ()

    @classmethod
    def text_item(cls, kind: ConversationItemKind, text: str) -> "ConversationItem":
        return cls(kind=kind, text=text)

    @classmethod
    def assistant(
        cls, text: str, calls: list[ToolCall] | None = None
    ) -> "ConversationItem":
        """构造一个可同时包含正文和工具调用的模型回合。"""
        return cls(
            kind=ConversationItemKind.ASSISTANT,
            text=text,
            calls=tuple(calls or ()),
        )

    @classmethod
    def tool_calls(cls, calls: list[ToolCall]) -> "ConversationItem":
        return cls(kind=ConversationItemKind.TOOL_CALLS, calls=tuple(calls))

    @classmethod
    def tool_results(cls, results: list[ToolResult]) -> "ConversationItem":
        return cls(kind=ConversationItemKind.TOOL_RESULTS, results=tuple(results))


class StreamEventKind(str, Enum):
    """统一增量事件类型。

    注意：无 thinking 事件——思考增量在 Provider 内被识别后直接丢弃，
    不进入事件流（对应 spec F5「接收即丢弃、不混入正文」）。
    """

    TEXT_DELTA = "text_delta"
    TOOL_CALL = "tool_call"
    USAGE = "usage"
    ERROR = "error"
    DONE = "done"


@dataclass(frozen=True)
class TokenUsage:
    """单轮或会话当前已知的 Token 用量快照。"""

    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class StreamEvent:
    """Provider 与上层之间的唯一数据契约。"""

    kind: StreamEventKind
    text: str = ""
    call: ToolCall | None = None
    usage: TokenUsage | None = None

    @classmethod
    def text_delta(cls, s: str) -> "StreamEvent":
        return cls(StreamEventKind.TEXT_DELTA, s)

    @classmethod
    def tool_call(cls, call: ToolCall) -> "StreamEvent":
        return cls(StreamEventKind.TOOL_CALL, call=call)

    @classmethod
    def token_usage(cls, usage: TokenUsage) -> "StreamEvent":
        return cls(StreamEventKind.USAGE, usage=usage)

    @classmethod
    def error(cls, msg: str) -> "StreamEvent":
        return cls(StreamEventKind.ERROR, msg)

    @classmethod
    def done(cls) -> "StreamEvent":
        return cls(StreamEventKind.DONE)


class AgentEventKind(str, Enum):
    """Agent Loop 对界面公开的事件类型。"""

    USER_MESSAGE = "user_message"
    TEXT_DELTA = "text_delta"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    USAGE_UPDATED = "usage_updated"
    ITERATION_STARTED = "iteration_started"
    ROUND_FINISHED = "round_finished"
    ERROR = "error"
    TASK_FINISHED = "task_finished"


class StopReason(str, Enum):
    """一次 Agent 任务的停止原因。"""

    COMPLETED = "completed"
    ITERATION_LIMIT = "iteration_limit"
    CANCELLED = "cancelled"
    UNKNOWN_TOOL_LIMIT = "unknown_tool_limit"
    STREAM_ERROR = "stream_error"


@dataclass(frozen=True)
class AgentEvent:
    """Agent Loop 与界面之间的唯一运行时数据契约。"""

    kind: AgentEventKind
    iteration: int = 0
    text: str = ""
    call: ToolCall | None = None
    result: ToolResult | None = None
    usage: TokenUsage | None = None
    stop_reason: StopReason | None = None

    @classmethod
    def user_message(cls, text: str) -> "AgentEvent":
        return cls(AgentEventKind.USER_MESSAGE, text=text)

    @classmethod
    def text_delta(cls, text: str, iteration: int) -> "AgentEvent":
        return cls(AgentEventKind.TEXT_DELTA, iteration=iteration, text=text)

    @classmethod
    def tool_started(cls, call: ToolCall, iteration: int) -> "AgentEvent":
        return cls(AgentEventKind.TOOL_STARTED, iteration=iteration, call=call)

    @classmethod
    def tool_finished(
        cls, call: ToolCall, result: ToolResult, iteration: int
    ) -> "AgentEvent":
        return cls(
            AgentEventKind.TOOL_FINISHED,
            iteration=iteration,
            call=call,
            result=result,
        )

    @classmethod
    def usage_updated(cls, usage: TokenUsage, iteration: int) -> "AgentEvent":
        return cls(AgentEventKind.USAGE_UPDATED, iteration=iteration, usage=usage)

    @classmethod
    def iteration_started(cls, iteration: int) -> "AgentEvent":
        return cls(AgentEventKind.ITERATION_STARTED, iteration=iteration)

    @classmethod
    def round_finished(cls, iteration: int) -> "AgentEvent":
        return cls(AgentEventKind.ROUND_FINISHED, iteration=iteration)

    @classmethod
    def error(cls, text: str, iteration: int = 0) -> "AgentEvent":
        return cls(AgentEventKind.ERROR, iteration=iteration, text=text)

    @classmethod
    def task_finished(
        cls, reason: StopReason, iteration: int, text: str = ""
    ) -> "AgentEvent":
        return cls(
            AgentEventKind.TASK_FINISHED,
            iteration=iteration,
            text=text,
            stop_reason=reason,
        )
