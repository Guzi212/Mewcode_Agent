import os
from pathlib import Path

import pytest

from mewcode.shell import (
    current_shell_spec,
    minimal_shell_environment,
    platform_context,
)


def test_windows_host_shell_is_fixed_system_powershell(monkeypatch):
    if os.name != "nt":
        pytest.skip("需要 Windows 系统 PowerShell")
    monkeypatch.delenv("MEWCODE_SANDBOX", raising=False)

    spec = current_shell_spec("Windows")

    assert Path(spec.executable).name.lower() == "powershell.exe"
    assert spec.arguments_prefix == (
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
    )
    assert spec.shell_name == "Windows PowerShell"


def test_windows_command_sets_utf8_before_user_source(monkeypatch):
    if os.name != "nt":
        pytest.skip("需要 Windows 系统 PowerShell")
    monkeypatch.delenv("MEWCODE_SANDBOX", raising=False)

    command = current_shell_spec("Windows").command("Write-Output '中文'")

    assert "UTF8Encoding" in command[-1]
    assert "Set-Location -LiteralPath ([Environment]::CurrentDirectory)" in command[-1]
    assert command[-1].endswith("Write-Output '中文'")


def test_windows_appcontainer_uses_system_powershell(monkeypatch):
    if os.name != "nt":
        pytest.skip("需要 Windows 系统命令提示符")
    monkeypatch.setenv("MEWCODE_SANDBOX", "windows-appcontainer")

    spec = current_shell_spec("Windows")
    command = spec.command("Write-Output 'sandbox'")

    assert Path(spec.executable).name.lower() == "powershell.exe"
    assert spec.arguments_prefix == (
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
    )
    assert spec.shell_name == "Windows PowerShell"
    assert spec.encoding == "utf-8"
    assert "New-PSDrive -Name MewCodeWorkspace" in command[-1]
    assert "Set-Location 'MewCodeWorkspace:\\'" in command[-1]
    assert command[-1].endswith("Write-Output 'sandbox'")


def test_minimal_environment_excludes_credentials_and_proxies(monkeypatch):
    if os.name != "nt":
        pytest.skip("需要 Windows 系统 PowerShell")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://secret")

    environment = minimal_shell_environment(current_shell_spec("Windows"))

    assert "OPENAI_API_KEY" not in environment
    assert "HTTPS_PROXY" not in environment
    assert environment["PYTHONIOENCODING"] == "utf-8"


def test_appcontainer_environment_keeps_only_sandbox_marker(monkeypatch):
    if os.name != "nt":
        pytest.skip("需要 Windows 命令提示符")
    monkeypatch.setenv("MEWCODE_SANDBOX", "windows-appcontainer")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")

    environment = minimal_shell_environment(current_shell_spec("Windows"))

    assert environment["MEWCODE_SANDBOX"] == "windows-appcontainer"
    assert "OPENAI_API_KEY" not in environment


def test_unsupported_platform_context_is_explicit():
    assert platform_context("Linux") == "当前运行平台：Linux；命令 Shell：不可用。"


def test_macos_shell_contract_uses_fixed_posix_sh(monkeypatch):
    monkeypatch.setattr(Path, "is_file", lambda _path: True)

    spec = current_shell_spec("Darwin")

    assert spec.executable.replace("\\", "/") == "/bin/sh"
    assert spec.arguments_prefix == ("-c",)
    assert spec.platform_name == "macOS"
    assert spec.shell_name == "POSIX sh"
    command = spec.command("printf 'ok'")
    assert command[0].replace("\\", "/") == "/bin/sh"
    assert command[1:] == ["-c", "printf 'ok'"]
