"""沙箱工作进程内的文件查找与文本搜索。"""

from __future__ import annotations

import glob
from pathlib import Path

from .base import require_string
from .files import MAX_CHARS, _limit, worker_path
from .models import ToolCall, ToolResult

MAX_MATCHES = 100


def find_files(call: ToolCall, workspace: Path) -> ToolResult:
    pattern = require_string(call.arguments, "pattern")
    base_raw = call.arguments.get("path", ".")
    if not isinstance(base_raw, str):
        raise ValueError("参数 path 必须是字符串")
    base = worker_path(base_raw, workspace)
    paths = [Path(item) for item in glob.glob(str(base / pattern), recursive=True)]
    files = sorted(path for path in paths if path.is_file())
    truncated = len(files) > MAX_MATCHES
    output, char_truncated = _limit("\n".join(str(path) for path in files[:MAX_MATCHES]))
    truncated = truncated or char_truncated
    if not files:
        return ToolResult(call.id, call.name, True, "", "未找到匹配文件")
    return ToolResult(
        call.id,
        call.name,
        True,
        output,
        f"找到 {min(len(files), MAX_MATCHES)} 个文件{'，已截断' if truncated else ''}",
        truncated,
    )


def search_code(call: ToolCall, workspace: Path) -> ToolResult:
    pattern = require_string(call.arguments, "pattern")
    base_raw = call.arguments.get("path", ".")
    if not isinstance(base_raw, str):
        raise ValueError("参数 path 必须是字符串")
    base = worker_path(base_raw, workspace)
    if not base.exists():
        return ToolResult.failure(call, "path_not_found", f"搜索范围不存在：{base}")
    targets = [base] if base.is_file() else [path for path in base.rglob("*") if path.is_file()]
    matches: list[str] = []
    for path in targets:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(lines, 1):
            if pattern in line:
                matches.append(f"{path}:{line_number}: {line}")
                if len(matches) >= MAX_MATCHES:
                    break
        if len(matches) >= MAX_MATCHES:
            break
    if not matches:
        return ToolResult(call.id, call.name, True, "", "未找到匹配内容")
    output, char_truncated = _limit("\n".join(matches))
    truncated = len(matches) >= MAX_MATCHES or char_truncated
    return ToolResult(
        call.id,
        call.name,
        True,
        output,
        f"找到 {len(matches)} 处匹配{'，已截断' if truncated else ''}",
        truncated,
    )
