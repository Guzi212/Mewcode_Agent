"""按 protocol 分派构造具体 Provider。"""

from __future__ import annotations

from ..config import ProviderConfig
from ..errors import ProviderError
from .anthropic import AnthropicProvider
from .base import Provider
from .openai import OpenAIProvider


def create_provider(cfg: ProviderConfig) -> Provider:
    if cfg.protocol == "anthropic":
        return AnthropicProvider(cfg)
    if cfg.protocol == "openai":
        return OpenAIProvider(cfg)
    raise ProviderError(f"不支持的 protocol：{cfg.protocol}")
