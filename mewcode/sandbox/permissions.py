"""真实路径归一化与内存会话授权。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .models import AccessGrant, AccessMode, ApprovalScope


def resolve_path(raw_path: str | Path) -> Path:
    """展开用户目录并解析符号链接；不存在目标也保留其规范化路径。"""
    return Path(raw_path).expanduser().resolve(strict=False)


def resolve_target(raw_path: str | Path, workspace: str | Path) -> Path:
    """把相对工具路径绑定到工作目录后再解析真实路径。"""
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(workspace) / candidate
    return candidate.resolve(strict=False)


def _contains(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _allows(grant: AccessGrant, target: Path, mode: AccessMode) -> bool:
    if grant.mode == AccessMode.READ and mode != AccessMode.READ:
        return False
    if grant.mode == AccessMode.EXECUTE and mode != AccessMode.EXECUTE:
        return False
    if grant.target.is_dir():
        return _contains(grant.target, target)
    return grant.target == target


class PermissionStore:
    """保存当前会话的外部授权；默认工作区和临时目录可读写。"""

    def __init__(self, workspace: str | Path, temp_dir: str | Path | None = None) -> None:
        self.workspace = resolve_path(workspace)
        self.temp_dir = resolve_path(temp_dir or tempfile.gettempdir())
        self._session_grants: list[AccessGrant] = []

    def default_grants(self) -> tuple[AccessGrant, ...]:
        return (
            AccessGrant(self.workspace, AccessMode.WRITE, ApprovalScope.SESSION),
            AccessGrant(self.temp_dir, AccessMode.WRITE, ApprovalScope.SESSION),
        )

    def grants_for_call(self, once: AccessGrant | None = None) -> tuple[AccessGrant, ...]:
        grants = [*self.default_grants(), *self._session_grants]
        if once is not None:
            grants.append(once)
        return tuple(grants)

    def is_allowed(self, raw_path: str | Path, mode: AccessMode) -> bool:
        target = resolve_path(raw_path)
        return any(_allows(grant, target, mode) for grant in self.grants_for_call())

    def add(self, grant: AccessGrant) -> None:
        if grant.scope != ApprovalScope.SESSION:
            raise ValueError("只有会话授权可以保存")
        self._session_grants.append(
            AccessGrant(resolve_path(grant.target), grant.mode, grant.scope)
        )
