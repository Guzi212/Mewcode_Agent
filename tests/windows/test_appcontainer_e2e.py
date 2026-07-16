from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from mewcode.sandbox import (
    AccessGrant,
    AccessMode,
    ApprovalScope,
    SandboxRequest,
    SandboxState,
)
from mewcode.sandbox.native_components import (
    HELPER_FILENAME,
    MANIFEST_FILENAME,
    NativeComponentResolver,
)
from mewcode.sandbox.windows import WindowsSandbox
from mewcode.tools.models import ToolCall


pytestmark = pytest.mark.windows_native


def _request(
    workspace: Path,
    call: ToolCall,
    *extra_grants: AccessGrant,
) -> SandboxRequest:
    workspace_grant = AccessGrant(
        workspace,
        AccessMode.WRITE,
        ApprovalScope.SESSION,
    )
    return SandboxRequest(call, workspace, (workspace_grant, *extra_grants))


def _transaction_files() -> list[Path]:
    root = Path(os.environ["LOCALAPPDATA"]) / "MewCode" / "sandbox"
    return list(root.glob("*/transactions/*.json"))


def _process_alive(pid: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(0x0010_0000 | 0x1000, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259
    finally:
        kernel32.CloseHandle(handle)


async def _wait_until(predicate, *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("等待 Windows 原生测试状态超时")


async def test_timeout_kills_child_and_grandchild_then_next_command_recovers(
    windows_native: WindowsSandbox,
    tmp_path: Path,
):
    workspace = tmp_path / "process-tree"
    workspace.mkdir()
    (workspace / "grandchild.py").write_text(
        "from pathlib import Path\n"
        "import os, time\n"
        "root = Path(__file__).resolve().parent\n"
        "(root / 'grandchild.pid').write_text(str(os.getpid()), encoding='utf-8')\n"
        "(root / 'tree-ready').write_text('ready', encoding='utf-8')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    python_executable = str(Path(sys.executable).resolve()).replace("'", "''")
    (workspace / "child.ps1").write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "[IO.File]::WriteAllText([IO.Path]::Combine($PSScriptRoot, 'child.pid'), "
        "[string]$PID)\n"
        "try {\n"
        "  $info = [Diagnostics.ProcessStartInfo]::new()\n"
        f"  $info.FileName = '{python_executable}'\n"
        "  $info.UseShellExecute = $false\n"
        "  $script = [IO.Path]::Combine($PSScriptRoot, 'grandchild.py')\n"
        "  $info.Arguments = '-S \"' + $script + '\"'\n"
        "  $grand = [Diagnostics.Process]::Start($info)\n"
        "  $grand.WaitForExit()\n"
        "} catch {\n"
        "  [IO.File]::WriteAllText([IO.Path]::Combine($PSScriptRoot, "
        "'grandchild-error.txt'), $_.Exception.Message)\n"
        "  [IO.File]::WriteAllText([IO.Path]::Combine($PSScriptRoot, "
        "'tree-ready'), 'error')\n"
        "  Start-Sleep -Seconds 30\n"
        "}\n",
        encoding="utf-8",
    )
    command = (
        "$script = Join-Path ([Environment]::CurrentDirectory) 'child.ps1'; "
        "$quotedScript = [char]34 + $script + [char]34; "
        "$args = @('-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy',"
        "'Bypass','-File',$quotedScript); "
        "$child = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') "
        "-ArgumentList $args -PassThru; "
        "[IO.File]::WriteAllText((Join-Path ([Environment]::CurrentDirectory) "
        "'child-launch.pid'), [string]$child.Id); "
        "$ready = Join-Path ([Environment]::CurrentDirectory) 'tree-ready'; "
        "while (-not (Test-Path -LiteralPath $ready)) { "
        "Start-Sleep -Milliseconds 50 }; "
        "Start-Sleep -Seconds 30"
    )

    result = await windows_native.run(
        _request(
            workspace,
            ToolCall("tree-timeout", "run_command", {"command": command}),
        ),
        8.0,
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "sandbox_timeout"
    child_pid = int((workspace / "child-launch.pid").read_text(encoding="utf-8"))
    error_file = workspace / "grandchild-error.txt"
    assert not error_file.exists(), (
        error_file.read_text(encoding="utf-8") if error_file.exists() else ""
    )
    grandchild_pid = int((workspace / "grandchild.pid").read_text(encoding="utf-8"))
    await _wait_until(
        lambda: not _process_alive(child_pid) and not _process_alive(grandchild_pid),
        timeout=5,
    )

    recovered = await windows_native.run(
        _request(
            workspace,
            ToolCall(
                "after-timeout",
                "run_command",
                {"command": "Write-Output 'recovered'"},
            ),
        ),
        5.0,
    )
    assert recovered.ok
    assert "recovered" in recovered.output
    assert _transaction_files() == []


async def test_cancel_kills_job_recovers_acl_and_keeps_backend_usable(
    windows_native: WindowsSandbox,
    tmp_path: Path,
):
    workspace = tmp_path / "cancel"
    workspace.mkdir()
    command = (
        "[IO.File]::WriteAllText((Join-Path ([Environment]::CurrentDirectory) "
        "'cancel-child.pid'), [string]$PID); Start-Sleep -Seconds 30"
    )
    task = asyncio.create_task(
        windows_native.run(
            _request(
                workspace,
                ToolCall("cancel", "run_command", {"command": command}),
            ),
            30.0,
        )
    )
    pid_file = workspace / "cancel-child.pid"
    await _wait_until(lambda: pid_file.is_file(), timeout=15)
    command_pid = int(pid_file.read_text(encoding="utf-8"))

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await _wait_until(lambda: not _process_alive(command_pid), timeout=5)
    assert _transaction_files() == []
    assert windows_native.diagnose().state is SandboxState.READY
    recovered = await windows_native.run(
        _request(
            workspace,
            ToolCall(
                "after-cancel",
                "run_command",
                {"command": "Write-Output 'cancel recovered'"},
            ),
        ),
        5.0,
    )
    assert recovered.ok


async def test_host_exit_is_detected_and_cleans_job_and_acl(
    windows_native: WindowsSandbox,
    tmp_path: Path,
):
    workspace = tmp_path / "host-exit"
    workspace.mkdir()
    helper = NativeComponentResolver().resolve_windows_helper()
    request_id = "host-exit-native"
    request = {
        "protocol_version": 1,
        "operation": "run",
        "request_id": request_id,
        "timeout_ms": 60_000,
        "python_executable": str(Path(sys.executable).resolve()),
        "workspace": str(workspace.resolve()),
        "grants": [],
        "worker_payload": {
            "call": {
                "id": request_id,
                "name": "run_command",
                "arguments": {
                    "command": (
                        "[IO.File]::WriteAllText((Join-Path "
                        "([Environment]::CurrentDirectory) 'host-child.pid'), "
                        "[string]$PID); Start-Sleep -Seconds 30"
                    )
                },
            },
            "workspace": str(workspace.resolve()),
        },
    }
    request_path = workspace / "request.jsonl"
    request_path.write_text(
        json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    driver = workspace / "host_driver.py"
    driver.write_text(
        "from pathlib import Path\n"
        "import subprocess, sys, time\n"
        "helper, request = sys.argv[1:3]\n"
        "process = subprocess.Popen([helper], stdin=subprocess.PIPE, "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "process.stdin.write(Path(request).read_bytes())\n"
        "process.stdin.close()\n"
        "print(process.pid, flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    host = subprocess.Popen(
        [sys.executable, str(driver), str(helper), str(request_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert host.stdout is not None
    helper_pid = int(host.stdout.readline().strip())
    child_pid_file = workspace / "host-child.pid"
    try:
        await _wait_until(
            lambda: child_pid_file.is_file() and bool(_transaction_files()),
            timeout=15,
        )
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        host.terminate()
        host.wait(timeout=5)
        await _wait_until(
            lambda: not _process_alive(helper_pid)
            and not _process_alive(child_pid)
            and not _transaction_files(),
            timeout=10,
        )
    finally:
        if host.poll() is None:
            host.kill()
            host.wait(timeout=5)

    assert windows_native.diagnose().state is SandboxState.READY


async def test_single_file_read_grant_does_not_open_sibling_or_write(
    windows_native: WindowsSandbox,
    tmp_path: Path,
):
    workspace = tmp_path / "grant-workspace"
    external = tmp_path / "grant-external"
    workspace.mkdir()
    external.mkdir()
    allowed = external / "allowed.txt"
    sibling = external / "sibling.txt"
    allowed.write_text("allowed sentinel", encoding="utf-8")
    sibling.write_text("sibling sentinel", encoding="utf-8")
    read_grant = AccessGrant(allowed, AccessMode.READ, ApprovalScope.ONCE)

    read = await windows_native.run(
        _request(
            workspace,
            ToolCall("grant-read", "read_file", {"path": str(allowed)}),
            read_grant,
        ),
        5.0,
    )
    sibling_read = await windows_native.run(
        _request(
            workspace,
            ToolCall("sibling-read", "read_file", {"path": str(sibling)}),
            read_grant,
        ),
        5.0,
    )
    write = await windows_native.run(
        _request(
            workspace,
            ToolCall(
                "grant-write",
                "write_file",
                {"path": str(allowed), "content": "unexpected"},
            ),
            read_grant,
        ),
        5.0,
    )

    assert read.ok and "allowed sentinel" in read.output
    assert not sibling_read.ok
    assert sibling_read.error is not None
    assert sibling_read.error.code == "file_read_error"
    assert not write.ok
    assert write.error is not None
    assert write.error.code == "file_write_error"
    assert allowed.read_text(encoding="utf-8") == "allowed sentinel"
    assert sibling.read_text(encoding="utf-8") == "sibling sentinel"
    assert _transaction_files() == []


async def test_large_workspace_uses_inherited_acl_without_target_limit(
    windows_native: WindowsSandbox,
    tmp_path: Path,
):
    workspace = tmp_path / "large-workspace"
    generated = workspace / ".venv" / "Lib" / "site-packages" / "fixture"
    generated.mkdir(parents=True)
    for index in range(4_100):
        (generated / f"module_{index:04}.py").write_text("fixture\n", encoding="utf-8")
    expected = generated / "module_4099.py"

    result = await windows_native.run(
        _request(
            workspace,
            ToolCall("large-workspace", "read_file", {"path": str(expected)}),
        ),
        15.0,
    )

    assert result.ok, result.error
    assert "fixture" in result.output
    assert _transaction_files() == []


def _copy_component(source: Path, target: Path) -> None:
    target.mkdir()
    shutil.copy2(source / HELPER_FILENAME, target / HELPER_FILENAME)
    shutil.copy2(source / MANIFEST_FILENAME, target / MANIFEST_FILENAME)


def _rewrite_manifest(root: Path, **changes: object) -> None:
    path = root / MANIFEST_FILENAME
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.update(changes)
    path.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")


def test_isolated_component_tamper_matrix_and_restore(
    windows_native: WindowsSandbox,
    tmp_path: Path,
):
    source = NativeComponentResolver().resolve_windows_helper().parent
    missing_development = tmp_path / "missing-development"

    tampered = tmp_path / "tampered"
    _copy_component(source, tampered)
    with (tampered / HELPER_FILENAME).open("ab") as stream:
        stream.write(b"tampered")
    diagnostic = WindowsSandbox(
        NativeComponentResolver(
            package_root=tampered,
            development_root=missing_development,
        )
    ).diagnose()
    assert diagnostic.code == "component_integrity_failed"

    wrong_arch = tmp_path / "wrong-arch"
    _copy_component(source, wrong_arch)
    helper = wrong_arch / HELPER_FILENAME
    data = bytearray(helper.read_bytes())
    pe_offset = int.from_bytes(data[0x3C:0x40], "little")
    data[pe_offset + 4 : pe_offset + 6] = (0xAA64).to_bytes(2, "little")
    helper.write_bytes(data)
    _rewrite_manifest(wrong_arch, sha256=hashlib.sha256(data).hexdigest())
    diagnostic = WindowsSandbox(
        NativeComponentResolver(
            package_root=wrong_arch,
            development_root=missing_development,
        )
    ).diagnose()
    assert diagnostic.code == "component_mismatch"

    wrong_version = tmp_path / "wrong-version"
    _copy_component(source, wrong_version)
    _rewrite_manifest(wrong_version, helper_version="9.9.9")
    diagnostic = WindowsSandbox(
        NativeComponentResolver(
            package_root=wrong_version,
            development_root=missing_development,
        )
    ).diagnose()
    assert diagnostic.code == "component_mismatch"

    restored = tmp_path / "restored"
    _copy_component(source, restored)
    diagnostic = WindowsSandbox(
        NativeComponentResolver(
            package_root=restored,
            development_root=missing_development,
        )
    ).diagnose()
    assert diagnostic.state is SandboxState.READY
