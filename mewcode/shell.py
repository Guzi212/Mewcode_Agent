"""显式的平台 Shell 选择与最小运行环境。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform


@dataclass(frozen=True)
class ShellSpec:
    executable: str
    arguments_prefix: tuple[str, ...]
    encoding: str = "utf-8"
    platform_name: str = ""
    shell_name: str = ""

    def command(self, source: str) -> list[str]:
        if self.platform_name == "Windows" and self.shell_name == "Windows PowerShell":
            if os.environ.get("MEWCODE_SANDBOX") == "windows-appcontainer":
                location_setup = (
                    "$mewcodeCwd = [Environment]::CurrentDirectory; "
                    "New-PSDrive -Name MewCodeWorkspace -PSProvider FileSystem "
                    "-Root $mewcodeCwd -Scope Global -ErrorAction Stop | Out-Null; "
                    "Set-Location 'MewCodeWorkspace:\\'; "
                )
            else:
                location_setup = (
                    "Set-Location -LiteralPath ([Environment]::CurrentDirectory); "
                )
            encoding_setup = (
                "$utf8 = [System.Text.UTF8Encoding]::new($false); "
                "[Console]::InputEncoding = $utf8; "
                "[Console]::OutputEncoding = $utf8; "
                "$OutputEncoding = $utf8; "
                + location_setup
            )
            source = encoding_setup + source
        return [self.executable, *self.arguments_prefix, source]


def current_shell_spec(system: str | None = None) -> ShellSpec:
    """返回固定系统 Shell；不搜索当前目录或工作区 PATH。"""
    system = system or platform.system()
    if system == "Windows":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        executable = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if not executable.is_absolute() or not executable.is_file():
            raise OSError("未找到系统 Windows PowerShell")
        return ShellSpec(
            str(executable),
            ("-NoLogo", "-NoProfile", "-NonInteractive", "-Command"),
            platform_name="Windows",
            shell_name="Windows PowerShell",
        )
    if system == "Darwin":
        executable = Path("/bin/sh")
        if not executable.is_file():
            raise OSError("未找到 macOS POSIX Shell")
        return ShellSpec(
            str(executable),
            ("-c",),
            platform_name="macOS",
            shell_name="POSIX sh",
        )
    raise OSError(f"不支持的平台 Shell：{system}")


def minimal_shell_environment(spec: ShellSpec) -> dict[str, str]:
    """仅保留 Shell/运行时所需值，明确排除凭据和代理变量。"""
    common = {"LANG", "LC_ALL", "LC_CTYPE", "TERM", "TEMP", "TMP"}
    windows = {
        "ALLUSERSPROFILE",
        "APPDATA",
        "CommonProgramFiles",
        "CommonProgramFiles(x86)",
        "CommonProgramW6432",
        "ComSpec",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "ProgramData",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "ProgramW6432",
        "PSModulePath",
        "SystemDrive",
        "SystemRoot",
        "USERPROFILE",
        "WINDIR",
    }
    allowed = common | (windows if spec.platform_name == "Windows" else {"PATH"})
    allowed_upper = {key.upper() for key in allowed}
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in allowed_upper and value
    }
    if spec.platform_name == "Windows":
        environment["PYTHONIOENCODING"] = "utf-8"
        if os.environ.get("MEWCODE_SANDBOX") == "windows-appcontainer":
            environment["MEWCODE_SANDBOX"] = "windows-appcontainer"
    return environment


def platform_context(system: str | None = None) -> str:
    requested = system or platform.system()
    try:
        spec = current_shell_spec(requested)
    except OSError:
        return f"当前运行平台：{requested}；命令 Shell：不可用。"
    return f"当前运行平台：{spec.platform_name}；命令 Shell：{spec.shell_name}。"
