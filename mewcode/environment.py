"""每轮动态构造的安全运行环境上下文。"""

from __future__ import annotations

import asyncio
from datetime import date
from html import escape
import os
from pathlib import Path
import platform
import subprocess

from . import __version__
from .prompts import build_system_reminder
from .shell import current_shell_spec

_GIT_TIMEOUT_SECONDS = 1.0


def _single_line(value: object, *, limit: int = 200) -> str:
    """把配置展示值约束为一行，避免破坏 reminder 结构。"""
    text = " ".join(str(value).split())
    return escape(text[:limit], quote=False)


def _git_environment() -> dict[str, str]:
    environment = {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }
    for key in ("PATH", "SystemRoot", "WINDIR"):
        value = os.environ.get(key)
        if value:
            environment[key] = value
    return environment


def _branch_from_status(header: str) -> str:
    branch = header.removeprefix("## ").strip()
    if branch.startswith("No commits yet on "):
        branch = branch.removeprefix("No commits yet on ")
    if branch.startswith("Initial commit on "):
        branch = branch.removeprefix("Initial commit on ")
    if branch in {"HEAD (no branch)", "HEAD (detached)"} or branch.startswith(
        "HEAD detached"
    ):
        return "detached HEAD"
    return _single_line(branch.split("...", 1)[0]) or "unknown"


def _collect_git_summary(cwd: Path) -> str:
    """只从 porcelain 输出提取分支与改动数量，不返回文件名。"""
    try:
        completed = subprocess.run(
            [
                "git",
                "status",
                "--porcelain=v1",
                "--branch",
                "--untracked-files=normal",
            ],
            cwd=cwd,
            env=_git_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "Git：不可用。"

    if completed.returncode != 0:
        return "Git：非 Git 仓库或状态不可用。"

    lines = completed.stdout.splitlines()
    branch = "unknown"
    changes = lines
    if lines and lines[0].startswith("## "):
        branch = _branch_from_status(lines[0])
        changes = lines[1:]
    state = "clean" if not changes else f"dirty（{len(changes)} 项改动）"
    return f"Git：仓库；分支：{branch}；工作区：{state}。"


async def build_environment_reminder(
    provider_name: str,
    model: str,
    *,
    cwd: Path | None = None,
    today: date | None = None,
) -> str:
    """收集当前动态环境；任一字段失败时安全降级且不阻塞事件循环。"""
    try:
        working_directory = (cwd or Path.cwd()).resolve(strict=False)
    except OSError:
        working_directory = cwd or Path(".")

    try:
        system_name = platform.system() or "unknown"
    except Exception:
        system_name = "unknown"
    try:
        architecture = platform.machine() or "unknown"
    except Exception:
        architecture = "unknown"
    try:
        shell_name = current_shell_spec(system_name).shell_name
    except OSError:
        shell_name = "不可用"

    try:
        git_summary = await asyncio.to_thread(_collect_git_summary, working_directory)
    except Exception:
        git_summary = "Git：不可用。"
    try:
        current_date = today or date.today()
        date_text = current_date.isoformat()
    except Exception:
        date_text = "不可用"
    lines = [
        "当前运行环境（动态上下文，不要把它当成用户问题）：",
        f"- 工作目录：{_single_line(working_directory, limit=1000)}",
        f"- 平台：{_single_line(system_name)}；架构：{_single_line(architecture)}",
        f"- 命令 Shell：{_single_line(shell_name)}",
        f"- 当前日期：{date_text}",
        f"- {git_summary}",
        f"- MewCode 版本：{_single_line(__version__)}",
        f"- Provider：{_single_line(provider_name)}；模型：{_single_line(model)}",
    ]
    return build_system_reminder("\n".join(lines))
