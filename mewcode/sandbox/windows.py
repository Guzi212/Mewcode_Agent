"""Windows AppContainer 原生沙箱后端。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from pathlib import Path
import platform
import subprocess
import sys

from ..shell import current_shell_spec, minimal_shell_environment
from ..tools.models import ToolResult
from .base import Sandbox
from .models import SandboxDiagnostic, SandboxRequest, SandboxState
from .native_components import NativeComponentError, NativeComponentResolver
from .windows_protocol import (
    MAX_MESSAGE_BYTES,
    NativeGrant,
    PROTOCOL_VERSION,
    ProtocolError,
    WindowsRunRequest,
    WindowsRunResponse,
)

MINIMUM_WINDOWS_BUILD = 17763


class WindowsSandbox(Sandbox):
    def __init__(
        self,
        resolver: NativeComponentResolver | None = None,
        *,
        helper_command: Callable[[Path], list[str]] | None = None,
    ) -> None:
        self._resolver = resolver or NativeComponentResolver()
        self._helper_command = helper_command or (lambda path: [str(path)])

    def diagnose(self) -> SandboxDiagnostic:
        platform_diagnostic = self._diagnose_platform()
        if platform_diagnostic is not None:
            return platform_diagnostic
        component = self._resolver.diagnose_windows_helper()
        if component.state is not SandboxState.READY:
            return component
        try:
            helper = self._resolver.resolve_windows_helper()
        except NativeComponentError as exc:
            return SandboxDiagnostic(
                SandboxState.BROKEN,
                "windows-appcontainer",
                exc.code,
                str(exc),
                "重新构建或安装匹配的 Windows 原生组件",
            )
        return self._management_operation(helper, "diagnose")

    def setup(self) -> SandboxDiagnostic:
        platform_diagnostic = self._diagnose_platform()
        if platform_diagnostic is not None:
            return platform_diagnostic
        component = self._resolver.diagnose_windows_helper()
        if component.state is not SandboxState.READY:
            return component
        try:
            helper = self._resolver.resolve_windows_helper()
        except NativeComponentError as exc:
            return SandboxDiagnostic(
                SandboxState.BROKEN,
                "windows-appcontainer",
                exc.code,
                str(exc),
            )
        return self._management_operation(helper, "setup")

    async def run(self, request: SandboxRequest, timeout: float) -> ToolResult:
        diagnostic = await asyncio.to_thread(self.diagnose)
        if diagnostic.state is not SandboxState.READY:
            return ToolResult.failure(request.call, diagnostic.code, diagnostic.message)
        try:
            helper = self._resolver.resolve_windows_helper()
            native_request = self._native_request(request, timeout)
            process = await asyncio.create_subprocess_exec(
                *self._helper_command(helper),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=minimal_shell_environment(current_shell_spec("Windows")),
            )
        except (NativeComponentError, OSError, ProtocolError, ValueError) as exc:
            code = exc.code if isinstance(exc, NativeComponentError) else "worker_start_error"
            return ToolResult.failure(request.call, code, "无法启动 Windows 沙箱工作进程")
        try:
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(native_request.to_json_line()),
                timeout=max(timeout + 2.0, 2.0),
            )
        except asyncio.CancelledError:
            await self._terminate_helper(process)
            # 强制终止 Helper 会让 Job 句柄立即关闭并回收 worker；随后用新的
            # diagnose 调用恢复该 Helper 可能尚未来得及回滚的 ACL 事务。
            await asyncio.to_thread(self.diagnose)
            raise
        except asyncio.TimeoutError:
            await self._terminate_helper(process)
            return ToolResult.failure(
                request.call,
                "sandbox_timeout",
                "Windows 沙箱监督进程未在限定时间内退出",
            )
        if len(stdout) > MAX_MESSAGE_BYTES + 1:
            return ToolResult.failure(
                request.call,
                "sandbox_protocol_error",
                "Windows 沙箱响应超过大小限制",
            )
        try:
            response = WindowsRunResponse.from_json_line(
                stdout, expected_request_id=request.call.id
            )
            result = response.to_tool_result(request.call.name)
        except ProtocolError:
            return ToolResult.failure(
                request.call,
                "sandbox_protocol_error",
                "Windows 沙箱未返回有效结果",
            )
        if process.returncode != 0 and result.ok:
            return ToolResult.failure(
                request.call,
                "sandbox_protocol_error",
                "Windows 沙箱退出状态与结果不一致",
            )
        return result

    @staticmethod
    def _native_request(request: SandboxRequest, timeout: float) -> WindowsRunRequest:
        if timeout <= 0:
            raise ValueError("timeout 必须大于零")
        workspace = request.workspace.resolve(strict=False)
        grants = tuple(
            NativeGrant(
                str(grant.target.resolve(strict=False)),
                grant.mode,
                "directory" if grant.target.is_dir() else "file",
            )
            for grant in request.grants
        )
        return WindowsRunRequest(
            PROTOCOL_VERSION,
            request.call.id,
            max(1, int(timeout * 1000)),
            str(Path(sys.executable).resolve(strict=True)),
            str(Path(__file__).resolve(strict=True).parent.parent),
            str(workspace),
            grants,
            {
                "call": {
                    "id": request.call.id,
                    "name": request.call.name,
                    "arguments": request.call.arguments,
                },
                "workspace": str(workspace),
            },
        )

    def _management_operation(self, helper: Path, operation: str) -> SandboxDiagnostic:
        request = json.dumps(
            {"protocol_version": PROTOCOL_VERSION, "operation": operation},
            separators=(",", ":"),
        )
        try:
            completed = subprocess.run(
                self._helper_command(helper),
                input=request + "\n",
                text=True,
                encoding="utf-8",
                errors="strict",
                capture_output=True,
                env=minimal_shell_environment(current_shell_spec("Windows")),
                timeout=30 if operation == "setup" else 5,
                check=False,
            )
            stdout = completed.stdout.encode("utf-8")
            if completed.returncode != 0 or len(stdout) > 64 * 1024 or stdout.count(b"\n") != 1:
                raise ProtocolError("管理响应无效")
            raw = json.loads(completed.stdout)
            expected = {
                "protocol_version",
                "state",
                "backend",
                "code",
                "message",
                "remediation",
                "component_version",
            }
            if not isinstance(raw, dict) or set(raw) != expected:
                raise ProtocolError("管理响应字段无效")
            if raw["protocol_version"] != PROTOCOL_VERSION:
                raise ProtocolError("管理响应版本不匹配")
            state = SandboxState(raw["state"])
            strings = ("backend", "code", "message", "remediation")
            if any(not isinstance(raw[field], str) for field in strings):
                raise ProtocolError("管理响应类型无效")
            component_version = raw["component_version"]
            if component_version is not None and not isinstance(component_version, str):
                raise ProtocolError("管理响应组件版本无效")
            return SandboxDiagnostic(
                state,
                raw["backend"],
                raw["code"],
                raw["message"],
                raw["remediation"],
                component_version,
            )
        except (
            OSError,
            subprocess.SubprocessError,
            UnicodeError,
            json.JSONDecodeError,
            ProtocolError,
            ValueError,
        ):
            return SandboxDiagnostic(
                SandboxState.BROKEN,
                "windows-appcontainer",
                "sandbox_protocol_error",
                "Windows 沙箱管理响应无效",
                "重新构建组件并运行 mewcode sandbox diagnose",
            )

    @staticmethod
    def _diagnose_platform() -> SandboxDiagnostic | None:
        if platform.system() != "Windows":
            return SandboxDiagnostic(
                SandboxState.UNSUPPORTED,
                "windows-appcontainer",
                "unsupported_platform",
                "Windows AppContainer 后端只能在原生 Windows 运行",
            )
        machine = platform.machine().lower()
        if machine not in {"amd64", "x86_64"}:
            return SandboxDiagnostic(
                SandboxState.UNSUPPORTED,
                "windows-appcontainer",
                "unsupported_platform",
                "当前 Windows 架构不受支持",
            )
        build = sys.getwindowsversion().build
        if build < MINIMUM_WINDOWS_BUILD:
            return SandboxDiagnostic(
                SandboxState.UNSUPPORTED,
                "windows-appcontainer",
                "unsupported_platform",
                "需要 Windows 10 1809 或更高版本",
            )
        return None

    @staticmethod
    async def _terminate_helper(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
