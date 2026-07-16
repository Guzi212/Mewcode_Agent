"""命令行入口：启动 TUI，或执行显式沙箱管理命令。"""

from __future__ import annotations

import platform
import sys
from collections.abc import Sequence

from .config import load_config
from .errors import MewCodeError
from .sandbox import SandboxDiagnostic, SandboxFactory, SandboxState
from .tui.app import MewCodeApp


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        _configure_windows_stdio()
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        return _management_command(arguments)
    try:
        config = load_config()
        MewCodeApp(config).run()
        return 0
    except MewCodeError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1


def _configure_windows_stdio() -> None:
    if platform.system() != "Windows":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _management_command(arguments: list[str]) -> int:
    if arguments == ["sandbox", "diagnose"]:
        diagnostic = SandboxFactory.create().diagnose()
        _print_diagnostic(diagnostic)
        return 0 if diagnostic.state is SandboxState.READY else 1
    if arguments == ["sandbox", "setup"]:
        if platform.system() != "Windows":
            print("错误：sandbox setup 仅支持原生 Windows", file=sys.stderr)
            return 1
        backend = SandboxFactory.create()
        setup = getattr(backend, "setup", None)
        if setup is None:
            print("错误：当前沙箱后端不支持 setup", file=sys.stderr)
            return 1
        diagnostic = setup()
        _print_diagnostic(diagnostic)
        return 0 if diagnostic.state is SandboxState.READY else 1
    print(
        "用法：mewcode [sandbox diagnose|sandbox setup]",
        file=sys.stderr,
    )
    return 2


def _print_diagnostic(diagnostic: SandboxDiagnostic) -> None:
    print(f"后端：{diagnostic.backend}")
    print(f"状态：{diagnostic.state.value}")
    print(f"代码：{diagnostic.code}")
    print(f"消息：{diagnostic.message}")
    if diagnostic.remediation:
        print(f"建议：{diagnostic.remediation}")
    if diagnostic.component_version:
        print(f"组件版本：{diagnostic.component_version}")
