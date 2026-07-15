"""Provider 统一抽象接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from ..config import ProviderConfig
from ..messages import ConversationItem, ConversationItemKind, Message, StreamEvent
from ..tools.models import ToolDefinition


class Provider(ABC):
    """与协议无关的对话后端抽象。

    子类把自家 SSE 归一化为统一的 StreamEvent；识别到思考增量应丢弃、不产出；
    网络/HTTP 错误须在内部捕获转成 StreamEvent.error(...) 后正常收束，不向外抛。
    """

    def __init__(self, cfg: ProviderConfig) -> None:
        self._cfg = cfg

    @property
    def name(self) -> str:
        return self._cfg.name

    @property
    def model(self) -> str:
        return self._cfg.model

    @abstractmethod
    def stream(
        self,
        history: list[ConversationItem] | list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """传入完整历史与可选工具定义，异步产出文本和完整工具调用事件。"""
        ...


def normalize_history(history: list[ConversationItem] | list[Message]) -> list[ConversationItem]:
    """兼容旧的纯文本 Message 调用方，供逐步迁移使用。"""
    if not history or isinstance(history[0], ConversationItem):
        return history  # type: ignore[return-value]
    role_map = {
        "system": ConversationItemKind.SYSTEM,
        "user": ConversationItemKind.USER,
        "assistant": ConversationItemKind.ASSISTANT,
    }
    return [ConversationItem.text_item(role_map[item.role.value], item.content) for item in history]  # type: ignore[union-attr]
