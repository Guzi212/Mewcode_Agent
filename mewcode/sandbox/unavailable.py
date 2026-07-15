"""无法施加系统级边界时的失败关闭后端。"""

from __future__ import annotations

from ..tools.models import ToolResult
from .base import Sandbox
from .models import SandboxRequest


class UnavailableSandbox(Sandbox):
    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def run(self, request: SandboxRequest, timeout: float) -> ToolResult:
        return ToolResult.failure(request.call, "sandbox_unavailable", self.reason)
