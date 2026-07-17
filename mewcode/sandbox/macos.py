"""macOS Seatbelt 沙箱后端。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys

from ..tools.models import ToolResult
from .base import Sandbox
from .models import SandboxDiagnostic, SandboxRequest, SandboxState


class MacOSSandbox(Sandbox):
    """Seatbelt 工作进程启动器。

    在工具工作进程协议就绪前，缺少必要启动条件时按失败关闭处理。
    """

    async def run(self, request: SandboxRequest, timeout: float) -> ToolResult:
        if shutil.which("sandbox-exec") is None:
            return ToolResult.failure(
                request.call,
                "sandbox_unavailable",
                "未找到 macOS Seatbelt 启动器，拒绝无隔离执行工具",
            )
        return await asyncio.to_thread(self._run_sync, request, timeout)

    def _run_sync(self, request: SandboxRequest, timeout: float) -> ToolResult:
        payload = {
            "call": {
                "id": request.call.id,
                "name": request.call.name,
                "arguments": request.call.arguments,
            },
            "workspace": str(request.workspace),
        }
        command = [
            "sandbox-exec",
            "-p",
            self._profile(request),
            sys.executable,
            "-m",
            "mewcode.tool_worker",
        ]
        try:
            completed = subprocess.run(
                command,
                input=json.dumps(payload) + "\n",
                text=True,
                capture_output=True,
                env=self._clean_env(),
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.failure(request.call, "timeout", "工具沙箱执行超时")
        except OSError as exc:
            return ToolResult.failure(request.call, "sandbox_unavailable", f"无法启动 Seatbelt：{exc}")
        stdout = completed.stdout.strip()
        if not stdout:
            stderr = completed.stderr.strip()
            detail = stderr or f"Seatbelt 工作进程异常退出（退出码 {completed.returncode}）"
            return ToolResult.failure(
                request.call,
                "sandbox_unavailable",
                f"Seatbelt 沙箱工作进程未能启动：{detail}",
            )
        try:
            raw = json.loads(stdout)
            error = raw.get("error")
            from ..tools.models import ToolError

            return ToolResult(
                call_id=str(raw["call_id"]),
                name=str(raw["name"]),
                ok=bool(raw["ok"]),
                output=str(raw.get("output", "")),
                summary=str(raw.get("summary", "")),
                truncated=bool(raw.get("truncated", False)),
                error=None
                if error is None
                else ToolError(str(error["code"]), str(error["message"])),
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            detail = completed.stderr.strip() or str(exc)
            code = (
                "sandbox_unavailable"
                if "sandbox_apply" in detail or "Operation not permitted" in detail
                else "sandbox_protocol_error"
            )
            return ToolResult.failure(
                request.call,
                code,
                f"沙箱工作进程未返回有效结果：{detail}",
            )

    @staticmethod
    def _quote(path: Path) -> str:
        return str(path).replace("\\", "\\\\").replace('"', '\\"')

    def _profile(self, request: SandboxRequest) -> str:
        """生成最小 Seatbelt 规则；工具路径由 grants 精确控制。"""
        read_roots = {
            Path(sys.prefix).resolve(),
            Path(sys.base_prefix).resolve(),
            Path("/System"),
            Path("/usr/lib"),
            # APFS 的 firmlink / 符号链接会把 /bin、/sbin 解析到这里；只
            # 授权表面路径会令 Seatbelt 在 exec 阶段中止（SIGABRT/-6）。
            Path("/usr/bin"),
            Path("/usr/sbin"),
            Path("/usr/share"),
            Path("/bin"),
            Path("/sbin"),
            Path("/opt/homebrew"),
            # /bin/sh 在 macOS 上会读取该系统选择文件；不给会产生无害但
            # 误导性的 stderr，影响命令结果的可读性。
            Path("/private/var/select"),
        }
        rules = [
            "(version 1)",
            "(deny default)",
            "(allow process-fork)",
            "(allow process-exec)",
            # Seatbelt 解析绝对路径时需要读取根目录本身。literal 不会放开
            # / 的子树；缺少该规则会在 exec 阶段触发 SIGABRT（退出码 -6）。
            '(allow file-read* (literal "/"))',
        ]
        rules.extend(
            f'(allow file-read* (subpath "{self._quote(root)}"))'
            for root in sorted(read_roots, key=str)
            if root.exists()
        )
        # realpath(3) 会逐级读取祖先目录的元数据。仅授权子目录不足以
        # 启动位于工作区 .venv 中的解释器；这里不授予祖先目录内容读取权。
        metadata_roots = {
            parent
            for root in read_roots
            for parent in root.resolve(strict=False).parents
        }
        rules.extend(
            f'(allow file-read-metadata (literal "{self._quote(root)}"))'
            for root in sorted(metadata_roots, key=str)
            if root != Path("/")
        )
        for grant in request.grants:
            target = grant.target.resolve(strict=False)
            rules.extend(
                f'(allow file-read-metadata (literal "{self._quote(parent)}"))'
                for parent in target.parents
                if parent != Path("/")
            )
            matcher = "subpath" if target.is_dir() else "literal"
            quoted = self._quote(target)
            rules.append(f'(allow file-read* ({matcher} "{quoted}"))')
            if grant.mode.value == "write":
                rules.append(f'(allow file-write* ({matcher} "{quoted}"))')
        return "\n".join(rules)

    @staticmethod
    def _clean_env() -> dict[str, str]:
        allowed = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM"}
        return {key: value for key, value in __import__("os").environ.items() if key in allowed}

    def diagnose(self) -> SandboxDiagnostic:
        if shutil.which("sandbox-exec") is None:
            return SandboxDiagnostic(
                SandboxState.BROKEN,
                "macos-seatbelt",
                "component_missing",
                "未找到 macOS Seatbelt 启动器",
                "确认当前系统为受支持的 macOS 13 或更高版本",
            )
        return SandboxDiagnostic(
            SandboxState.READY,
            "macos-seatbelt",
            "ready",
            "macOS Seatbelt 沙箱可用",
        )
