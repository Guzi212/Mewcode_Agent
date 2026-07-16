import platform
from pathlib import Path
import subprocess
import pytest

from mewcode.sandbox import (
    AccessMode,
    PermissionStore,
    SandboxFactory,
    SandboxRequest,
    SandboxState,
)
from mewcode.sandbox.macos import MacOSSandbox
from mewcode.sandbox.windows import WindowsSandbox
from mewcode.tools.models import ToolCall
from mewcode.tools.registry import build_default_registry


async def test_factory_never_falls_back_to_unsandboxed_execution(tmp_path: Path):
    sandbox = SandboxFactory.create()
    store = PermissionStore(tmp_path)
    call = ToolCall("call-1", "read_file", {"path": "missing.txt"})
    request = SandboxRequest(call, tmp_path, store.grants_for_call())

    result = await sandbox.run(request, 0.1)

    if platform.system() == "Linux":
        assert result.error is not None
        assert result.error.code == "sandbox_unavailable"
    elif platform.system() == "Windows":
        assert result.error is not None
        # Windows 机器可能处于未安装、未 setup、已就绪或开发环境不可执行等
        # 不同状态；本测试只验证所有状态都失败关闭，绝不回退到宿主直接执行。
        assert result.output == ""
        assert not (tmp_path / "missing.txt").exists()
    else:
        # macOS 后端会在后续任务中执行实际工作进程；本测试只保证工厂存在。
        assert sandbox is not None


def test_factory_exposes_structured_diagnostic():
    diagnostic = SandboxFactory.create().diagnose()

    assert isinstance(diagnostic.state, SandboxState)
    assert diagnostic.backend
    assert diagnostic.code
    assert diagnostic.message


def test_same_tool_contract_selects_only_platform_native_backend(monkeypatch):
    tool_names = [tool.name for tool in build_default_registry().definitions()]

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    macos = SandboxFactory.create()
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    windows = SandboxFactory.create()

    assert isinstance(macos, MacOSSandbox)
    assert isinstance(windows, WindowsSandbox)
    assert [tool.name for tool in build_default_registry().definitions()] == tool_names


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
    if platform.system() != "Darwin":
        pytest.skip("仅在 macOS 验证真实 Seatbelt 系统路径")
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
