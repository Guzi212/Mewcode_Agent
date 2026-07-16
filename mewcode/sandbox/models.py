"""系统级沙箱与外部路径授权的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..tools.models import ToolCall


class AccessMode(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"


class ApprovalScope(str, Enum):
    DENY = "deny"
    ONCE = "once"
    SESSION = "session"


class SandboxState(str, Enum):
    """宿主沙箱当前是否可以安全执行工具。"""

    READY = "ready"
    SETUP_REQUIRED = "setup_required"
    UNSUPPORTED = "unsupported"
    BROKEN = "broken"


@dataclass(frozen=True)
class SandboxDiagnostic:
    """供 CLI、TUI 和测试共用的脱敏沙箱状态。"""

    state: SandboxState
    backend: str
    code: str
    message: str
    remediation: str = ""
    component_version: str | None = None


@dataclass(frozen=True)
class AccessRequest:
    tool_name: str
    target: Path
    mode: AccessMode
    command_summary: str | None = None


@dataclass(frozen=True)
class AccessGrant:
    target: Path
    mode: AccessMode
    scope: ApprovalScope


@dataclass(frozen=True)
class SandboxRequest:
    call: ToolCall
    workspace: Path
    grants: tuple[AccessGrant, ...]
