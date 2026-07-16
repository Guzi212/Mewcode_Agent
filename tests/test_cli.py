import mewcode.cli as cli
from mewcode.config import AppConfig, ProviderConfig
from mewcode.errors import ConfigError
from mewcode.sandbox import SandboxDiagnostic, SandboxState


class ReconfigurableStream:
    def __init__(self):
        self.options = None

    def reconfigure(self, **options):
        self.options = options


def test_windows_entrypoint_configures_utf8_stdio(monkeypatch):
    stdout = ReconfigurableStream()
    stderr = ReconfigurableStream()
    monkeypatch.setattr(cli.platform, "system", lambda: "Windows")
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli.sys, "stderr", stderr)

    cli._configure_windows_stdio()

    assert stdout.options == {"encoding": "utf-8", "errors": "replace"}
    assert stderr.options == {"encoding": "utf-8", "errors": "replace"}


def test_main_config_error(monkeypatch, capsys):
    def boom():
        raise ConfigError("no config file")

    monkeypatch.setattr(cli, "load_config", boom)
    rc = cli.main([])
    assert rc == 1
    assert "no config file" in capsys.readouterr().err


def test_main_success(monkeypatch):
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda: AppConfig([ProviderConfig("a", "anthropic", "m", "k")]),
    )
    monkeypatch.setattr(cli.MewCodeApp, "run", lambda self: None)
    assert cli.main([]) == 0


class FakeSandbox:
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        self.setup_called = False

    def diagnose(self):
        return self.diagnostic

    def setup(self):
        self.setup_called = True
        return self.diagnostic


def diagnostic(state=SandboxState.READY):
    return SandboxDiagnostic(
        state,
        "windows-appcontainer",
        state.value,
        "脱敏消息",
        "修复建议",
        "0.1.0",
    )


def test_sandbox_diagnose_prints_stable_fields(monkeypatch, capsys):
    backend = FakeSandbox(diagnostic())
    monkeypatch.setattr(cli.SandboxFactory, "create", lambda: backend)
    assert cli.main(["sandbox", "diagnose"]) == 0
    output = capsys.readouterr().out
    assert "后端：windows-appcontainer" in output
    assert "状态：ready" in output
    assert "代码：ready" in output
    assert "组件版本：0.1.0" in output


def test_sandbox_diagnose_returns_failure_when_not_ready(monkeypatch):
    backend = FakeSandbox(diagnostic(SandboxState.SETUP_REQUIRED))
    monkeypatch.setattr(cli.SandboxFactory, "create", lambda: backend)
    assert cli.main(["sandbox", "diagnose"]) == 1


def test_sandbox_setup_is_explicit_and_windows_only(monkeypatch, capsys):
    backend = FakeSandbox(diagnostic())
    monkeypatch.setattr(cli.platform, "system", lambda: "Windows")
    monkeypatch.setattr(cli.SandboxFactory, "create", lambda: backend)
    assert cli.main(["sandbox", "setup"]) == 0
    assert backend.setup_called is True

    backend.setup_called = False
    monkeypatch.setattr(cli.platform, "system", lambda: "Darwin")
    assert cli.main(["sandbox", "setup"]) == 1
    assert backend.setup_called is False
    assert "仅支持原生 Windows" in capsys.readouterr().err


def test_unknown_management_command_does_not_start_tui(monkeypatch, capsys):
    started = False

    def start(_self):
        nonlocal started
        started = True

    monkeypatch.setattr(cli.MewCodeApp, "run", start)
    assert cli.main(["sandbox", "unknown"]) == 2
    assert started is False
    assert "用法" in capsys.readouterr().err
