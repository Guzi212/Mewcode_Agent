"""Windows 原生沙箱后端。

不可用时明确拒绝，绝不退化为宿主机执行。
"""

from __future__ import annotations

from ..tools.models import ToolResult
from .base import Sandbox
from .models import SandboxRequest


class WindowsSandbox(Sandbox):
    async def run(self, request: SandboxRequest, timeout: float) -> ToolResult:
        return ToolResult.failure(
            request.call,
            "sandbox_unavailable",
            "Windows 原生沙箱后端尚不可用，拒绝无隔离执行工具",
        )
