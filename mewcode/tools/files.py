"""沙箱工作进程内的文本文件操作。"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

from .base import require_string
from .models import ToolCall, ToolResult

MAX_LINES = 400
MAX_CHARS = 12_000


def worker_path(raw_path: str, workspace: Path) -> Path:
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else workspace / path


def _limit(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_CHARS:
        return text, False
    return f"{text[:MAX_CHARS]}\n…（输出已截断）", True


def read_file(call: ToolCall, workspace: Path) -> ToolResult:
    path = worker_path(require_string(call.arguments, "path"), workspace)
    try:
        if not path.is_file():
            return ToolResult.failure(
                call,
                "file_not_found",
                f"文件不存在或不是普通文件：{path}",
            )
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return ToolResult.failure(call, "not_text", f"文件不是 UTF-8 文本：{path}")
    except OSError as exc:
        return ToolResult.failure(call, "file_read_error", f"无法读取文件：{exc}")

    truncated = len(lines) > MAX_LINES
    selected = lines[:MAX_LINES]
    output, char_truncated = _limit("\n".join(f"{index}: {line}" for index, line in enumerate(selected, 1)))
    truncated = truncated or char_truncated
    if truncated and not output.endswith("输出已截断）"):
        output += "\n…（输出已截断）"
    return ToolResult(
        call.id,
        call.name,
        True,
        output,
        f"读取 {path.name}（{len(selected)} 行{'，已截断' if truncated else ''}）",
        truncated,
    )


def write_file(call: ToolCall, workspace: Path) -> ToolResult:
    path = worker_path(require_string(call.arguments, "path"), workspace)
    content = call.arguments.get("content")
    if not isinstance(content, str):
        raise ValueError("参数 content 必须是字符串")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
    except OSError as exc:
        return ToolResult.failure(call, "file_write_error", f"无法写入文件：{exc}")
    return ToolResult(call.id, call.name, True, "", f"已写入 {path}（{len(content)} 字符）")


def edit_file(call: ToolCall, workspace: Path) -> ToolResult:
    path = worker_path(require_string(call.arguments, "path"), workspace)
    old_text = require_string(call.arguments, "old_text")
    new_text = require_string(call.arguments, "new_text")
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ToolResult.failure(call, "file_not_found", f"文件不存在：{path}")
    except UnicodeDecodeError:
        return ToolResult.failure(call, "not_text", f"文件不是 UTF-8 文本：{path}")
    except OSError as exc:
        return ToolResult.failure(call, "file_read_error", f"无法读取文件：{exc}")

    count = content.count(old_text)
    if count != 1:
        return ToolResult.failure(
            call,
            "edit_match_count",
            f"原文片段匹配 {count} 次，必须恰好匹配 1 次；文件未修改",
        )
    replacement = content.replace(old_text, new_text, 1)
    write_call = ToolCall(call.id, "write_file", {"path": str(path), "content": replacement})
    written = write_file(write_call, workspace)
    if not written.ok:
        return ToolResult.failure(call, written.error.code, written.error.message)
    return ToolResult(call.id, call.name, True, "", f"已唯一替换 {path.name} 中的 1 处文本")
