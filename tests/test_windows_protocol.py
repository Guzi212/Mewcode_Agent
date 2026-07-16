import json

import pytest

from mewcode.sandbox import AccessMode
from mewcode.sandbox.windows_protocol import (
    NativeGrant,
    PROTOCOL_VERSION,
    ProtocolError,
    RunnerStatus,
    WindowsRunRequest,
    WindowsRunResponse,
)


def _request(**changes):
    values = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "call-1",
        "timeout_ms": 20_000,
        "python_executable": r"C:\Python\python.exe",
        "python_package_root": r"D:\project\mewcode",
        "workspace": r"D:\project",
        "grants": (NativeGrant(r"D:\project", AccessMode.WRITE, "directory"),),
        "worker_payload": {"call": {"id": "call-1"}, "workspace": r"D:\project"},
    }
    values.update(changes)
    return WindowsRunRequest(**values)


def _completed_response(**changes):
    raw = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "call-1",
        "status": "completed",
        "worker_result": {
            "call_id": "call-1",
            "name": "read_file",
            "ok": True,
            "output": "ok",
            "summary": "完成",
            "truncated": False,
            "error": None,
        },
        "error": None,
    }
    raw.update(changes)
    return (json.dumps(raw, ensure_ascii=False) + "\n").encode()


def test_request_serializes_as_one_strict_json_line():
    data = _request().to_json_line()
    raw = json.loads(data)

    assert data.endswith(b"\n")
    assert data.count(b"\n") == 1
    assert raw["operation"] == "run"
    assert raw["python_package_root"] == r"D:\project\mewcode"
    assert raw["grants"] == [
        {"path": r"D:\project", "mode": "write", "kind": "directory"}
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"protocol_version": PROTOCOL_VERSION + 1},
        {"request_id": ""},
        {"timeout_ms": True},
        {"timeout_ms": 0},
        {"python_executable": "python.exe"},
        {"python_package_root": "mewcode"},
        {"workspace": r"\\server\share"},
    ],
)
def test_request_rejects_invalid_boundary_fields(changes):
    with pytest.raises(ProtocolError):
        _request(**changes).to_json_line()


def test_completed_response_converts_to_tool_result():
    response = WindowsRunResponse.from_json_line(
        _completed_response(), expected_request_id="call-1"
    )
    result = response.to_tool_result("read_file")

    assert response.status is RunnerStatus.COMPLETED
    assert result.ok
    assert result.output == "ok"


def test_failed_response_converts_to_tool_error():
    raw = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "call-1",
        "status": "timed_out",
        "worker_result": None,
        "error": {"code": "sandbox_timeout", "message": "执行超时"},
    }
    response = WindowsRunResponse.from_json_line(
        (json.dumps(raw) + "\n").encode(), expected_request_id="call-1"
    )

    result = response.to_tool_result("run_command")

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "sandbox_timeout"


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"{}",
        b"{}\n{}\n",
        b"\xff\n",
        (json.dumps({"unexpected": True}) + "\n").encode(),
        _completed_response(request_id="other"),
        _completed_response(status="unknown"),
    ],
)
def test_response_rejects_malformed_or_mismatched_data(data):
    with pytest.raises(ProtocolError):
        WindowsRunResponse.from_json_line(data, expected_request_id="call-1")


def test_worker_result_rejects_extra_fields():
    raw = json.loads(_completed_response())
    raw["worker_result"]["extra"] = True
    response = WindowsRunResponse.from_json_line(
        (json.dumps(raw) + "\n").encode(), expected_request_id="call-1"
    )

    with pytest.raises(ProtocolError):
        response.to_tool_result("read_file")
