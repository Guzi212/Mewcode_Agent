"""Windows 原生 Helper 的窄化、严格且版本化 JSON 协议。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import PureWindowsPath
from typing import Literal

from ..tools.models import ToolError, ToolResult
from .models import AccessMode

PROTOCOL_VERSION = 2
MAX_MESSAGE_BYTES = 4 * 1024 * 1024


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class NativeGrant:
    path: str
    mode: AccessMode
    kind: Literal["file", "directory"]

    def to_dict(self) -> dict[str, str]:
        _require_windows_path(self.path, "grant.path")
        if self.kind not in {"file", "directory"}:
            raise ProtocolError("grant.kind 必须是 file 或 directory")
        return {"path": self.path, "mode": self.mode.value, "kind": self.kind}


@dataclass(frozen=True)
class WindowsRunRequest:
    protocol_version: int
    request_id: str
    timeout_ms: int
    python_executable: str
    python_package_root: str
    workspace: str
    grants: tuple[NativeGrant, ...]
    worker_payload: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        _require_protocol_version(self.protocol_version)
        _require_id(self.request_id)
        if isinstance(self.timeout_ms, bool) or not isinstance(self.timeout_ms, int):
            raise ProtocolError("timeout_ms 必须是整数")
        if not 1 <= self.timeout_ms <= 24 * 60 * 60 * 1000:
            raise ProtocolError("timeout_ms 超出允许范围")
        _require_windows_path(self.python_executable, "python_executable")
        _require_windows_path(self.python_package_root, "python_package_root")
        _require_windows_path(self.workspace, "workspace")
        if not isinstance(self.worker_payload, dict):
            raise ProtocolError("worker_payload 必须是对象")
        return {
            "protocol_version": self.protocol_version,
            "operation": "run",
            "request_id": self.request_id,
            "timeout_ms": self.timeout_ms,
            "python_executable": self.python_executable,
            "python_package_root": self.python_package_root,
            "workspace": self.workspace,
            "grants": [grant.to_dict() for grant in self.grants],
            "worker_payload": self.worker_payload,
        }

    def to_json_line(self) -> bytes:
        try:
            payload = json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ProtocolError(f"请求无法序列化：{exc}") from exc
        if len(payload) > MAX_MESSAGE_BYTES:
            raise ProtocolError("请求超过大小限制")
        return payload + b"\n"


class RunnerStatus(str, Enum):
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class RunnerError:
    code: str
    message: str


@dataclass(frozen=True)
class WindowsRunResponse:
    protocol_version: int
    request_id: str
    status: RunnerStatus
    worker_result: dict[str, object] | None
    error: RunnerError | None

    @classmethod
    def from_json_line(
        cls, data: bytes, *, expected_request_id: str
    ) -> "WindowsRunResponse":
        if not data or len(data) > MAX_MESSAGE_BYTES + 1:
            raise ProtocolError("响应为空或超过大小限制")
        if not data.endswith(b"\n") or data.count(b"\n") != 1:
            raise ProtocolError("响应必须是单行 JSON")
        try:
            text = data[:-1].decode("utf-8", errors="strict")
            raw = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("响应不是有效 UTF-8 JSON") from exc
        if not isinstance(raw, dict):
            raise ProtocolError("响应顶层必须是对象")
        _require_exact_keys(
            raw,
            {"protocol_version", "request_id", "status", "worker_result", "error"},
            "响应",
        )
        version = raw["protocol_version"]
        _require_protocol_version(version)
        request_id = raw["request_id"]
        _require_id(request_id)
        if request_id != expected_request_id:
            raise ProtocolError("响应 request_id 不匹配")
        try:
            status = RunnerStatus(raw["status"])
        except (TypeError, ValueError) as exc:
            raise ProtocolError("未知 Runner 状态") from exc
        worker_result = raw["worker_result"]
        raw_error = raw["error"]
        if status is RunnerStatus.COMPLETED:
            if not isinstance(worker_result, dict) or raw_error is not None:
                raise ProtocolError("completed 响应必须只包含 worker_result")
            error = None
        else:
            if worker_result is not None or not isinstance(raw_error, dict):
                raise ProtocolError("非 completed 响应必须只包含 error")
            _require_exact_keys(raw_error, {"code", "message"}, "error")
            code = raw_error["code"]
            message = raw_error["message"]
            if not isinstance(code, str) or not code or not isinstance(message, str) or not message:
                raise ProtocolError("error code/message 必须是非空字符串")
            error = RunnerError(code, message)
        return cls(version, request_id, status, worker_result, error)

    def to_tool_result(self, call_name: str) -> ToolResult:
        if self.status is not RunnerStatus.COMPLETED or self.worker_result is None:
            if self.error is None:
                raise ProtocolError("失败响应缺少 error")
            return ToolResult(
                self.request_id,
                call_name,
                False,
                "",
                self.error.message,
                False,
                ToolError(self.error.code, self.error.message),
            )
        raw = self.worker_result
        _require_exact_keys(
            raw,
            {"call_id", "name", "ok", "output", "summary", "truncated", "error"},
            "worker_result",
        )
        call_id = raw["call_id"]
        name = raw["name"]
        ok = raw["ok"]
        output = raw["output"]
        summary = raw["summary"]
        truncated = raw["truncated"]
        raw_error = raw["error"]
        if call_id != self.request_id or name != call_name:
            raise ProtocolError("worker_result 调用标识不匹配")
        if not isinstance(ok, bool) or not isinstance(output, str) or not isinstance(summary, str):
            raise ProtocolError("worker_result 字段类型错误")
        if not isinstance(truncated, bool):
            raise ProtocolError("worker_result.truncated 必须是布尔值")
        error: ToolError | None = None
        if raw_error is not None:
            if not isinstance(raw_error, dict):
                raise ProtocolError("worker_result.error 必须是对象或 null")
            _require_exact_keys(raw_error, {"code", "message"}, "worker_result.error")
            code = raw_error["code"]
            message = raw_error["message"]
            if not isinstance(code, str) or not isinstance(message, str):
                raise ProtocolError("worker_result.error 字段类型错误")
            error = ToolError(code, message)
        if ok == (error is not None):
            raise ProtocolError("worker_result.ok 与 error 不一致")
        return ToolResult(call_id, name, ok, output, summary, truncated, error)


def _require_protocol_version(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != PROTOCOL_VERSION:
        raise ProtocolError("协议版本不匹配")


def _require_id(value: object) -> None:
    if not isinstance(value, str) or not value or len(value) > 256 or "\x00" in value:
        raise ProtocolError("request_id 非法")


def _require_windows_path(value: object, field: str) -> None:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ProtocolError(f"{field} 必须是非空路径")
    path = PureWindowsPath(value)
    if not path.is_absolute() or not path.drive or str(path).startswith("\\\\"):
        raise ProtocolError(f"{field} 必须是本地绝对 Windows 路径")


def _require_exact_keys(raw: dict[object, object], expected: set[str], label: str) -> None:
    if set(raw) != expected:
        raise ProtocolError(f"{label} 字段不完整或包含未知字段")
