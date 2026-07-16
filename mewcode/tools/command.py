"""沙箱工作进程内的命令执行。"""

from __future__ import annotations

from pathlib import Path
import os
import signal
import subprocess

from ..shell import current_shell_spec, minimal_shell_environment
from .base import require_string
from .files import _limit, worker_path
from .models import ToolCall, ToolResult

COMMAND_TIMEOUT_SECONDS = 20


def run_command(call: ToolCall, workspace: Path) -> ToolResult:
    command = require_string(call.arguments, "command")
    cwd_raw = call.arguments.get("cwd", ".")
    if not isinstance(cwd_raw, str):
        raise ValueError("参数 cwd 必须是字符串")
    cwd = worker_path(cwd_raw, workspace)
    if not cwd.is_dir():
        return ToolResult.failure(call, "cwd_not_found", f"命令工作目录不存在：{cwd}")
    try:
        shell = current_shell_spec()
        popen_options: dict[str, object] = {}
        if os.name != "nt":
            popen_options["start_new_session"] = True
        process = subprocess.Popen(
            shell.command(command),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=minimal_shell_environment(shell),
            **popen_options,
        )
        try:
            stdout_raw, stderr_raw = process.communicate(timeout=COMMAND_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.kill()
            process.communicate()
            return ToolResult.failure(
                call,
                "timeout",
                f"命令超过 {COMMAND_TIMEOUT_SECONDS} 秒，已终止",
                summary="命令执行超时，已终止",
            )
    except (OSError, ValueError) as exc:
        return ToolResult.failure(call, "command_start_error", f"无法启动命令：{exc}")

    stdout = stdout_raw.decode(shell.encoding, errors="replace")
    stderr = stderr_raw.decode(shell.encoding, errors="replace")

    stdout, out_truncated = _limit(stdout)
    stderr, err_truncated = _limit(stderr)
    output = f"stdout:\n{stdout}\n\nstderr:\n{stderr}\n\nexit_code: {process.returncode}"
    truncated = out_truncated or err_truncated
    summary = f"命令退出码 {process.returncode}{'，输出已截断' if truncated else ''}"
    if process.returncode != 0:
        return ToolResult.failure(
            call,
            "command_failed",
            output,
            summary=summary,
            output=output,
        )
    return ToolResult(call.id, call.name, True, output, summary, truncated)
