"""在系统级沙箱中运行一次工具请求的严格 JSON Lines worker。"""

from __future__ import annotations

import json
from pathlib import Path
import sys

from .tools.command import run_command
from .tools.files import edit_file, read_file, write_file
from .tools.models import ToolCall, ToolError, ToolResult
from .tools.search import find_files, search_code

MAX_MESSAGE_BYTES = 4 * 1024 * 1024

_HANDLERS = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "run_command": run_command,
    "find_files": find_files,
    "search_code": search_code,
}


def _serialize(result: ToolResult) -> dict[str, object]:
    return {
        "call_id": result.call_id,
        "name": result.name,
        "ok": result.ok,
        "output": result.output,
        "summary": result.summary,
        "truncated": result.truncated,
        "error": None
        if result.error is None
        else {"code": result.error.code, "message": result.error.message},
    }


def handle(payload: dict[str, object]) -> ToolResult:
    if set(payload) != {"call", "workspace"}:
        raise ValueError("请求字段不完整或包含未知字段")
    call_data = payload["call"]
    if not isinstance(call_data, dict) or set(call_data) != {
        "id",
        "name",
        "arguments",
    }:
        raise ValueError("call 字段不完整或包含未知字段")
    call_id = call_data["id"]
    call_name = call_data["name"]
    arguments = call_data["arguments"]
    workspace_value = payload["workspace"]
    if (
        not isinstance(call_id, str)
        or not call_id
        or len(call_id) > 256
        or "\x00" in call_id
        or not isinstance(call_name, str)
        or not call_name
        or not isinstance(arguments, dict)
        or not isinstance(workspace_value, str)
        or not workspace_value
        or "\x00" in workspace_value
    ):
        raise ValueError("请求字段类型无效")

    call = ToolCall(id=call_id, name=call_name, arguments=arguments)
    workspace = Path(workspace_value)
    if not workspace.is_absolute():
        raise ValueError("workspace 必须是绝对路径")
    handler = _HANDLERS.get(call.name)
    if handler is None:
        return ToolResult.failure(call, "unknown_tool", f"未知工具：{call.name}")
    try:
        return handler(call, workspace)
    except ValueError as exc:
        return ToolResult.failure(call, "invalid_arguments", str(exc))
    except Exception:
        # worker 信任边界：意外不回显异常、宿主路径或环境内容。
        return ToolResult(
            call.id,
            call.name,
            False,
            "",
            "工具内部错误",
            error=ToolError("internal_error", "工具内部错误"),
        )


def _parse_request(data: bytes) -> dict[str, object]:
    if (
        not data
        or len(data) > MAX_MESSAGE_BYTES + 1
        or not data.endswith(b"\n")
        or data.count(b"\n") != 1
    ):
        raise ValueError("请求必须是受限大小的单行 JSON")
    payload = json.loads(data[:-1].decode("utf-8", errors="strict"))
    if not isinstance(payload, dict):
        raise ValueError("请求必须是 JSON 对象")
    return payload


def _invalid_request_result() -> ToolResult:
    return ToolResult(
        "",
        "",
        False,
        "",
        "工具请求无效",
        error=ToolError("invalid_request", "工具请求无效"),
    )


def _encode_result(result: ToolResult) -> bytes:
    output = json.dumps(
        _serialize(result),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(output) <= MAX_MESSAGE_BYTES:
        return output + b"\n"
    limited = ToolResult(
        result.call_id,
        result.name,
        False,
        "",
        "工具输出超过限制",
        error=ToolError("output_limit", "工具输出超过限制"),
    )
    return (
        json.dumps(
            _serialize(limited),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def main() -> int:
    try:
        data = sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 2)
        result = handle(_parse_request(data))
    except (UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        result = _invalid_request_result()
    sys.stdout.buffer.write(_encode_result(result))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
