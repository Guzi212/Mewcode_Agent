import mewcode.cli as cli
from mewcode.config import AppConfig, ProviderConfig
from mewcode.errors import ConfigError


def test_main_config_error(monkeypatch, capsys):
    def boom():
        raise ConfigError("no config file")

    monkeypatch.setattr(cli, "load_config", boom)
    rc = cli.main()
    assert rc == 1
    assert "no config file" in capsys.readouterr().err


def test_main_success(monkeypatch):
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda: AppConfig([ProviderConfig("a", "anthropic", "m", "k")]),
    )
    monkeypatch.setattr(cli.MewCodeApp, "run", lambda self: None)
    assert cli.main() == 0
