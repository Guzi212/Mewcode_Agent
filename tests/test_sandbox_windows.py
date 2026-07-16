import asyncio
import json
from pathlib import Path
import platform

import pytest

from mewcode.sandbox import (
    AccessGrant,
    AccessMode,
    ApprovalScope,
    SandboxDiagnostic,
    SandboxRequest,
    SandboxState,
)
from mewcode.sandbox.windows import WindowsSandbox
from mewcode.sandbox.windows_protocol import PROTOCOL_VERSION
from mewcode.tools.models import ToolCall


class FakeResolver:
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        self.path = Path(r"C:\trusted\mewcode-windows-sandbox.exe")

    def diagnose_windows_helper(self):
        return self.diagnostic

    def resolve_windows_helper(self):
        return self.path


def _ready():
    return SandboxDiagnostic(
        SandboxState.READY,
        "windows-appcontainer",
        "ready",
        "ready",
        component_version="0.1.0",
    )


def _request(tmp_path):
    call = ToolCall("call-1", "read_file", {"path": "a.txt"})
    grant = AccessGrant(tmp_path, AccessMode.WRITE, ApprovalScope.SESSION)
    return SandboxRequest(call, tmp_path, (grant,))


def test_native_request_uses_actual_mewcode_package_root(tmp_path):
    native = WindowsSandbox._native_request(_request(tmp_path), 1)

    package_root = Path(native.python_package_root)
    assert package_root.name == "mewcode"
    assert (package_root / "tool_worker.py").is_file()


def test_diagnose_returns_component_failure_without_starting_helper(monkeypatch):
    if platform.system() != "Windows":
        pytest.skip("Windows 后端平台检查")
    broken = SandboxDiagnostic(
        SandboxState.BROKEN,
        "windows-appcontainer",
        "component_missing",
        "missing",
    )
    sandbox = WindowsSandbox(FakeResolver(broken))

    assert sandbox.diagnose() == broken


def test_management_diagnostic_is_strict(monkeypatch):
    if platform.system() != "Windows":
        pytest.skip("Windows 后端平台检查")
    raw = {
        "protocol_version": PROTOCOL_VERSION,
        "state": "setup_required",
        "backend": "windows-appcontainer",
        "code": "setup_required",
        "message": "需要准备",
        "remediation": "mewcode sandbox setup",
        "component_version": "0.1.0",
    }

    class Completed:
        returncode = 0
        stdout = json.dumps(raw) + "\n"

    monkeypatch.setattr("mewcode.sandbox.windows.subprocess.run", lambda *a, **k: Completed())
    sandbox = WindowsSandbox(FakeResolver(_ready()))

    diagnostic = sandbox.diagnose()

    assert diagnostic.state is SandboxState.SETUP_REQUIRED
    assert diagnostic.code == "setup_required"


class FakeProcess:
    def __init__(self, stdout: bytes, *, returncode=0, block=False):
        self.stdout_data = stdout
        self.returncode = None if block else returncode
        self.block = block
        self.terminated = False
        self.killed = False
        self._done = asyncio.Event()
        self.started = asyncio.Event()

    async def communicate(self, data):
        self.started.set()
        if self.block:
            await self._done.wait()
        return self.stdout_data, b""

    def terminate(self):
        self.terminated = True
        self.returncode = -1
        self._done.set()

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._done.set()

    async def wait(self):
        if self.block and self.returncode is None:
            await self._done.wait()
        return self.returncode


def _completed():
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
    return (json.dumps(raw) + "\n").encode()


async def test_run_maps_valid_helper_response(tmp_path, monkeypatch):
    if platform.system() != "Windows":
        pytest.skip("Windows 后端平台检查")
    sandbox = WindowsSandbox(FakeResolver(_ready()))
    monkeypatch.setattr(sandbox, "diagnose", lambda: _ready())
    process = FakeProcess(_completed())

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr("mewcode.sandbox.windows.asyncio.create_subprocess_exec", create)

    result = await sandbox.run(_request(tmp_path), 1)

    assert result.ok
    assert result.output == "ok"


async def test_cancel_terminates_helper_and_reraises(tmp_path, monkeypatch):
    if platform.system() != "Windows":
        pytest.skip("Windows 后端平台检查")
    sandbox = WindowsSandbox(FakeResolver(_ready()))
    diagnoses = 0

    def diagnose():
        nonlocal diagnoses
        diagnoses += 1
        return _ready()

    monkeypatch.setattr(sandbox, "diagnose", diagnose)
    process = FakeProcess(b"", block=True)

    async def create(*args, **kwargs):
        return process

    monkeypatch.setattr("mewcode.sandbox.windows.asyncio.create_subprocess_exec", create)
    task = asyncio.create_task(sandbox.run(_request(tmp_path), 10))
    await process.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminated
    assert diagnoses == 2
