import textwrap

import pytest

from mewcode.config import DEFAULT_AGENT_MAX_ITERATIONS, load_config
from mewcode.errors import ConfigError


def _write(tmp_path, content):
    p = tmp_path / "mewcode.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(p)


def test_load_multi_providers(tmp_path):
    p = _write(
        tmp_path,
        """
        providers:
          - name: a
            protocol: anthropic
            model: m1
            api_key: k1
            thinking: true
          - name: b
            protocol: openai
            model: m2
            api_key: k2
            base_url: https://x/v1
        """,
    )
    cfg = load_config(p)
    assert len(cfg.providers) == 2
    assert cfg.single() is None
    assert cfg.providers[0].thinking is True
    assert cfg.providers[1].base_url == "https://x/v1"


def test_single_provider(tmp_path):
    p = _write(
        tmp_path,
        """
        providers:
          - name: a
            protocol: anthropic
            model: m1
            api_key: k1
        """,
    )
    cfg = load_config(p)
    assert cfg.single() is not None
    assert cfg.single().name == "a"
    assert cfg.single().thinking is False
    assert cfg.agent_max_iterations == DEFAULT_AGENT_MAX_ITERATIONS


@pytest.mark.parametrize("value", [0, -1, "20", True, None])
def test_invalid_agent_max_iterations_falls_back(tmp_path, value):
    p = _write(
        tmp_path,
        f"""
        agent_max_iterations: {str(value).lower() if value is not None else 'null'}
        providers:
          - name: a
            protocol: anthropic
            model: m1
            api_key: k1
        """,
    )
    assert load_config(p).agent_max_iterations == DEFAULT_AGENT_MAX_ITERATIONS


def test_agent_max_iterations_can_be_configured(tmp_path):
    p = _write(
        tmp_path,
        """
        agent_max_iterations: 7
        providers:
          - name: a
            protocol: anthropic
            model: m1
            api_key: k1
        """,
    )
    assert load_config(p).agent_max_iterations == 7


def test_missing_api_key(tmp_path):
    p = _write(
        tmp_path,
        """
        providers:
          - name: a
            protocol: anthropic
            model: m1
        """,
    )
    with pytest.raises(ConfigError) as ei:
        load_config(p)
    assert "api_key" in str(ei.value)


def test_invalid_protocol(tmp_path):
    p = _write(
        tmp_path,
        """
        providers:
          - name: a
            protocol: gemini
            model: m1
            api_key: k1
        """,
    )
    with pytest.raises(ConfigError):
        load_config(p)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "nope.yaml"))
