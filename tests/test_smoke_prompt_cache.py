import pytest

from mewcode.config import ProviderConfig
from scripts.smoke_prompt_cache import _display_count, _select_provider


def test_smoke_displays_unknown_without_faking_zero():
    assert _display_count(None) == "unknown"
    assert _display_count(0) == "0"


def test_smoke_selects_single_or_named_provider():
    first = ProviderConfig("first", "openai", "m1", "k")
    second = ProviderConfig("second", "anthropic", "m2", "k")

    assert _select_provider([first], None) is first
    assert _select_provider([first, second], "second") is second
    with pytest.raises(ValueError, match="--provider"):
        _select_provider([first, second], None)
    with pytest.raises(ValueError, match="未找到"):
        _select_provider([first], "missing")
