"""在系统级沙箱中运行一次工具请求的 JSON Lines 工作进程。"""

from __future__ import annotations

import json
from pathlib import Path
import sys

from .tools.command import run_command
from .tools.files import edit_file, read_file, write_file
from .tools.models import ToolCall, ToolError, ToolResult
from .tools.search import find_files, search_code

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
    call_data = payload.get("call")
    if not isinstance(call_data, dict):
        raise ValueError("请求缺少 call 对象")
    call = ToolCall(
        id=str(call_data.get("id", "")),
        name=str(call_data.get("name", "")),
        arguments=dict(call_data.get("arguments", {})),
    )
    workspace = Path(str(payload.get("workspace", ".")))
    handler = _HANDLERS.get(call.name)
    if handler is None:
        return ToolResult.failure(call, "unknown_tool", f"未知工具：{call.name}")
    try:
        return handler(call, workspace)
    except ValueError as exc:
        return ToolResult.failure(call, "invalid_arguments", str(exc))
    except Exception as exc:  # 工作进程边界：任何意外均结构化回传。
        return ToolResult(
            call.id,
            call.name,
            False,
            "",
            "工具内部错误",
            error=ToolError("internal_error", f"工具内部错误：{exc}"),
        )


def main() -> int:
    try:
        payload = json.loads(sys.stdin.readline())
        if not isinstance(payload, dict):
            raise ValueError("请求必须是 JSON 对象")
        result = handle(payload)
    except Exception as exc:
        result = ToolResult("", "", False, "", "工具请求无效", error=ToolError("invalid_request", str(exc)))
    print(json.dumps(_serialize(result), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
