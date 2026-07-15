import pytest

from mewcode.config import ProviderConfig
from mewcode.errors import ProviderError
from mewcode.providers import create_provider
from mewcode.providers.anthropic import AnthropicProvider
from mewcode.providers.openai import OpenAIProvider


def test_create_anthropic():
    cfg = ProviderConfig(name="a", protocol="anthropic", model="m", api_key="k")
    assert isinstance(create_provider(cfg), AnthropicProvider)


def test_create_openai():
    cfg = ProviderConfig(name="o", protocol="openai", model="m", api_key="k")
    assert isinstance(create_provider(cfg), OpenAIProvider)


def test_invalid_protocol_raises():
    cfg = ProviderConfig(name="x", protocol="foo", model="m", api_key="k")
    with pytest.raises(ProviderError):
        create_provider(cfg)
