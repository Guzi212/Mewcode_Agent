"""单次会话的内存对话历史与上下文组装（不持久化）。"""

from __future__ import annotations

from collections.abc import Sequence

from .messages import ConversationItem, ConversationItemKind, Message, Role
from .tools.models import ToolCall, ToolResult


class Conversation:
    """维护单次会话内的对话历史，并把 system prompt 组装进请求上下文。"""

    def __init__(self, system_prompt: str) -> None:
        self.system_prompt = system_prompt
        self.messages: list[Message] = []
        self.items: list[ConversationItem] = []

    def add_user(self, text: str) -> None:
        self.messages.append(Message(Role.USER, text))
        self.items.append(ConversationItem.text_item(ConversationItemKind.USER, text))

    def add_assistant(self, text: str) -> None:
        self.messages.append(Message(Role.ASSISTANT, text))
        self.items.append(ConversationItem.assistant(text))

    def add_assistant_turn(
        self,
        text: str,
        calls: list[ToolCall],
        reasoning_content: str = "",
    ) -> None:
        """记录一个可同时包含前置文本、隐藏思考和工具调用的模型回合。"""
        if text:
            self.messages.append(Message(Role.ASSISTANT, text))
        self.items.append(
            ConversationItem.assistant(text, calls, reasoning_content)
        )

    def add_tool_calls(self, calls: list[ToolCall]) -> None:
        """记录首次模型响应中的完整工具调用批次。"""
        self.items.append(ConversationItem.tool_calls(calls))

    def add_tool_results(self, results: list[ToolResult]) -> None:
        """记录与工具调用顺序对应的执行结果批次。"""
        self.items.append(ConversationItem.tool_results(results))

    def build_context(self) -> list[Message]:
        """返回「system 消息 + 全部历史」，供 Provider.stream 使用。"""
        return [Message(Role.SYSTEM, self.system_prompt), *self.messages]

    def build_history(
        self,
        system_prompt: str | None = None,
        system_reminders: Sequence[str] = (),
    ) -> list[ConversationItem]:
        """返回稳定系统项、当前补充项与持久历史，不保存补充项。"""
        return [
            ConversationItem.text_item(
                ConversationItemKind.SYSTEM, system_prompt or self.system_prompt
            ),
            *(
                ConversationItem.system_reminder(reminder)
                for reminder in system_reminders
            ),
            *self.items,
        ]
