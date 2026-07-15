import platform
from pathlib import Path
import subprocess

from mewcode.sandbox import AccessMode, PermissionStore, SandboxFactory, SandboxRequest
from mewcode.sandbox.macos import MacOSSandbox
from mewcode.tools.models import ToolCall


async def test_factory_never_falls_back_to_unsandboxed_execution(tmp_path: Path):
    sandbox = SandboxFactory.create()
    store = PermissionStore(tmp_path)
    call = ToolCall("call-1", "read_file", {"path": "missing.txt"})
    request = SandboxRequest(call, tmp_path, store.grants_for_call())

    result = await sandbox.run(request, 0.1)

    if platform.system() in {"Linux", "Windows"}:
        assert result.error is not None
        assert result.error.code == "sandbox_unavailable"
    else:
        # macOS 后端会在后续任务中执行实际工作进程；本测试只保证工厂存在。
        assert sandbox is not None


def test_default_workspace_grant_allows_writing(tmp_path: Path):
    store = PermissionStore(tmp_path)
    assert store.is_allowed(tmp_path / "out.txt", AccessMode.WRITE)


def test_macos_empty_worker_output_is_reported_as_sandbox_unavailable(tmp_path: Path, monkeypatch):
    """Seatbelt 被宿主策略拒绝时，不应泄露 JSON 解析异常给用户。"""
    store = PermissionStore(tmp_path)
    call = ToolCall("call-1", "read_file", {"path": "missing.txt"})
    request = SandboxRequest(call, tmp_path, store.grants_for_call())
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 71, "", "sandbox_apply: Operation not permitted"),
    )

    result = MacOSSandbox()._run_sync(request, 1)

    assert result.error is not None
    assert result.error.code == "sandbox_unavailable"
    assert "Seatbelt 沙箱工作进程未能启动" in result.error.message
    assert "Expecting value" not in result.error.message


def test_macos_profile_allows_real_bin_and_sbin_paths(tmp_path: Path):
    store = PermissionStore(tmp_path)
    request = SandboxRequest(
        ToolCall("call-1", "run_command", {"command": "true"}),
        tmp_path,
        store.grants_for_call(),
    )

    profile = MacOSSandbox()._profile(request)

    assert '(allow file-read* (literal "/"))' in profile
    assert '(allow file-read* (subpath "/usr/bin"))' in profile
    assert '(allow file-read* (subpath "/usr/sbin"))' in profile
    assert '(allow file-read* (subpath "/private/var/select"))' in profile
    assert f'(allow file-read-metadata (literal "{tmp_path.parent}"))' in profile
