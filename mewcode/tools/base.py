"""工具抽象与通用参数校验。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from enum import Enum

from ..sandbox.models import AccessRequest
from .models import ToolCall, ToolDefinition, ToolResult


class ToolSafety(str, Enum):
    """工具是否可能产生外部副作用。"""

    READ_ONLY = "read_only"
    SIDE_EFFECT = "side_effect"


class Tool(ABC):
    """所有内置工具必须遵守的统一接口。"""

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """返回发送给模型的工具元信息。"""
        ...

    @property
    def safety(self) -> ToolSafety:
        """自定义工具默认按有副作用处理，避免误并发。"""
        return ToolSafety.SIDE_EFFECT

    @abstractmethod
    def access_request(self, call: ToolCall) -> AccessRequest | None:
        """根据参数返回所需外部路径访问；无需额外路径时返回 None。"""
        ...

    @abstractmethod
    async def execute(
        self,
        call: ToolCall,
        run_worker: Callable[[ToolCall], Awaitable[ToolResult]],
    ) -> ToolResult:
        """执行已经通过参数和授权检查的调用。"""
        ...


def require_string(arguments: dict[str, object], name: str) -> str:
    """获取非空字符串参数；调用方将 ValueError 转为结构化错误。"""
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"参数 {name} 必须是非空字符串")
    return value
