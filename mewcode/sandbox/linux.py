"""Linux/WSL Bubblewrap 沙箱后端。

完整命令封装将在工具工作进程接入后实现；缺少 bwrap 时必须失败关闭。
"""

from __future__ import annotations

import shutil

from ..tools.models import ToolResult
from .base import Sandbox
from .models import SandboxDiagnostic, SandboxRequest, SandboxState


class LinuxSandbox(Sandbox):
    def diagnose(self) -> SandboxDiagnostic:
        if shutil.which("bwrap") is None:
            return SandboxDiagnostic(
                SandboxState.UNSUPPORTED,
                "linux-bubblewrap",
                "unsupported_platform",
                "本版本尚未提供 Linux/WSL 沙箱后端",
                "请改用受支持的 macOS 或原生 Windows 环境",
            )
        return SandboxDiagnostic(
            SandboxState.UNSUPPORTED,
            "linux-bubblewrap",
            "unsupported_platform",
            "Linux Bubblewrap 后端尚未实现",
        )

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
