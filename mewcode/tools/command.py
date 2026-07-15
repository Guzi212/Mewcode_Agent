"""沙箱工作进程内的命令执行。"""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess

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
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=COMMAND_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate()
            return ToolResult.failure(
                call,
                "timeout",
                f"命令超过 {COMMAND_TIMEOUT_SECONDS} 秒，已终止",
                summary="命令执行超时，已终止",
            )
    except OSError as exc:
        return ToolResult.failure(call, "command_start_error", f"无法启动命令：{exc}")

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
