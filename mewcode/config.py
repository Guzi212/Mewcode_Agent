"""YAML 配置加载与校验（纯 YAML，不解析环境变量、不接受命令行 flag 覆盖）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .errors import ConfigError

VALID_PROTOCOLS = {"anthropic", "openai"}
DEFAULT_AGENT_MAX_ITERATIONS = 20

# 配置文件查找顺序：项目内优先，其次用户级
_CONFIG_PATHS = [
    Path("mewcode.yaml"),
    Path.home() / ".config" / "mewcode" / "config.yaml",
]


@dataclass
class ProviderConfig:
    """单个供应商配置，对应 YAML 一个条目的六字段。"""

    name: str
    protocol: str
    model: str
    api_key: str
    base_url: str | None = None
    thinking: bool = False


@dataclass
class AppConfig:
    """整个应用配置。"""

    providers: list[ProviderConfig]
    agent_max_iterations: int = DEFAULT_AGENT_MAX_ITERATIONS

    def single(self) -> ProviderConfig | None:
        """恰有一个 provider 时返回它，否则返回 None（需交互选择）。"""
        return self.providers[0] if len(self.providers) == 1 else None


def _find_config_path() -> Path:
    for p in _CONFIG_PATHS:
        if p.is_file():
            return p
    raise ConfigError(
        "未找到配置文件，请在 ./mewcode.yaml 或 ~/.config/mewcode/config.yaml "
        "创建（可参考 mewcode.yaml.example）"
    )


def _parse_provider(index: int, item: object) -> ProviderConfig:
    if not isinstance(item, dict):
        raise ConfigError(f"providers[{index}] 必须是映射")
    missing = [k for k in ("name", "protocol", "model", "api_key") if not item.get(k)]
    if missing:
        raise ConfigError(f"providers[{index}] 缺少必需字段：{', '.join(missing)}")
    protocol = str(item["protocol"])
    if protocol not in VALID_PROTOCOLS:
        raise ConfigError(
            f"providers[{index}] protocol 非法：{protocol}"
            f"（支持 {sorted(VALID_PROTOCOLS)}）"
        )
    return ProviderConfig(
        name=str(item["name"]),
        protocol=protocol,
        model=str(item["model"]),
        api_key=str(item["api_key"]),
        base_url=item.get("base_url"),
        thinking=bool(item.get("thinking", False)),
    )


def load_config(path: str | None = None) -> AppConfig:
    """加载并校验配置。

    path 仅用于测试注入具体文件；正常运行传 None，按 _CONFIG_PATHS 查找。
    任何非法情况抛 ConfigError（信息不含密钥值）。
    """
    if path is not None:
        cfg_path = Path(path)
        if not cfg_path.is_file():
            raise ConfigError(f"配置文件不存在：{cfg_path}")
    else:
        cfg_path = _find_config_path()

    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件 YAML 解析失败：{e}") from e

    if not isinstance(raw, dict) or "providers" not in raw:
        raise ConfigError("配置缺少顶层 providers 列表")
    items = raw["providers"]
    if not isinstance(items, list) or not items:
        raise ConfigError("providers 必须是非空列表")

    providers = [_parse_provider(i, item) for i, item in enumerate(items)]
    raw_limit = raw.get("agent_max_iterations", DEFAULT_AGENT_MAX_ITERATIONS)
    agent_max_iterations = (
        raw_limit
        if isinstance(raw_limit, int) and not isinstance(raw_limit, bool) and raw_limit > 0
        else DEFAULT_AGENT_MAX_ITERATIONS
    )
    return AppConfig(
        providers=providers,
        agent_max_iterations=agent_max_iterations,
    )
