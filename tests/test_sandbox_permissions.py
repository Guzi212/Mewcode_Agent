from pathlib import Path
import pytest

from mewcode.sandbox import AccessGrant, AccessMode, ApprovalScope, PermissionStore


def test_workspace_and_temp_are_writable_by_default(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = PermissionStore(workspace, tmp_path / "temp")
    (tmp_path / "temp").mkdir()

    assert store.is_allowed(workspace / "nested" / "file.txt", AccessMode.READ)
    assert store.is_allowed(workspace / "nested" / "file.txt", AccessMode.WRITE)


def test_read_session_grant_does_not_allow_writing(tmp_path: Path):
    workspace = tmp_path / "workspace"
    external = tmp_path / "external.txt"
    workspace.mkdir()
    external.write_text("hello")
    store = PermissionStore(workspace, tmp_path / "temp")
    (tmp_path / "temp").mkdir()
    store.add(AccessGrant(external, AccessMode.READ, ApprovalScope.SESSION))

    assert store.is_allowed(external, AccessMode.READ)
    assert not store.is_allowed(external, AccessMode.WRITE)


def test_symlink_target_is_checked_against_real_path(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external.txt"
    external.write_text("secret")
    link = workspace / "outside-link"
    try:
        link.symlink_to(external)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("当前 Windows 会话没有创建符号链接权限")
        raise
    store = PermissionStore(workspace, tmp_path / "temp")
    (tmp_path / "temp").mkdir()

    assert not store.is_allowed(link, AccessMode.READ)
