"""系统级沙箱抽象与平台后端选择。"""

from __future__ import annotations

from abc import ABC, abstractmethod
import platform

from ..tools.models import ToolResult
from .models import SandboxDiagnostic, SandboxRequest


class Sandbox(ABC):
    """在受限工作进程中执行一次工具调用。"""

    @abstractmethod
    def diagnose(self) -> SandboxDiagnostic:
        """只读检查后端状态，不执行准备或降级。"""
        ...

    @abstractmethod
    async def run(self, request: SandboxRequest, timeout: float) -> ToolResult:
        ...


class SandboxFactory:
    """只返回可施加系统边界的原生后端，不提供无隔离回退。"""

    @staticmethod
    def create() -> Sandbox:
        system = platform.system()
        if system == "Darwin":
            from .macos import MacOSSandbox

            return MacOSSandbox()
        if system == "Linux":
            from .linux import LinuxSandbox

            return LinuxSandbox()
        if system == "Windows":
            from .windows import WindowsSandbox

            return WindowsSandbox()
        from .unavailable import UnavailableSandbox

        return UnavailableSandbox(f"不支持的平台：{system}", backend=system.lower())
