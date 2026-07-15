"""Linux/WSL Bubblewrap 沙箱后端。

完整命令封装将在工具工作进程接入后实现；缺少 bwrap 时必须失败关闭。
"""

from __future__ import annotations

import shutil

from ..tools.models import ToolResult
from .base import Sandbox
from .models import SandboxRequest


class LinuxSandbox(Sandbox):
    async def run(self, request: SandboxRequest, timeout: float) -> ToolResult:
        if shutil.which("bwrap") is None:
            return ToolResult.failure(
                request.call,
                "sandbox_unavailable",
                "未找到 Bubblewrap（bwrap），拒绝无隔离执行工具",
            )
        return ToolResult.failure(
            request.call,
            "sandbox_unavailable",
            "Linux 沙箱工作进程尚未启动",
        )
