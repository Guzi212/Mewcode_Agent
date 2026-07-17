"""对真实 Provider 执行重复稳定前缀的缓存遥测 smoke。"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from time import monotonic

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mewcode.config import ProviderConfig, load_config  # noqa: E402
from mewcode.conversation import Conversation  # noqa: E402
from mewcode.environment import build_environment_reminder  # noqa: E402
from mewcode.errors import MewCodeError  # noqa: E402
from mewcode.messages import StreamEventKind, TokenUsage  # noqa: E402
from mewcode.prompts import SYSTEM_PROMPT  # noqa: E402
from mewcode.providers import create_provider  # noqa: E402
from mewcode.tools.registry import build_default_registry  # noqa: E402

_SMOKE_REQUEST = "这是缓存遥测请求。请只回复：缓存测试完成。不要调用工具。"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="重复发送相同稳定提示前缀并打印 Provider 缓存用量。"
    )
    parser.add_argument("--provider", help="配置中的 Provider 展示名称")
    parser.add_argument("--config", help="可选配置路径；默认使用 MewCode 查找顺序")
    parser.add_argument("--rounds", type=int, default=2, help="请求轮数，默认 2")
    return parser.parse_args()


def _select_provider(
    providers: list[ProviderConfig], requested_name: str | None
) -> ProviderConfig:
    if requested_name is not None:
        for provider in providers:
            if provider.name == requested_name:
                return provider
        raise ValueError("未找到指定 Provider")
    if len(providers) == 1:
        return providers[0]
    raise ValueError("配置包含多个 Provider，请使用 --provider 指定展示名称")


def _display_count(value: int | None) -> str:
    return "unknown" if value is None else str(value)


async def _run_smoke(config: ProviderConfig, rounds: int) -> int:
    provider = create_provider(config)
    conversation = Conversation(SYSTEM_PROMPT)
    tools = build_default_registry().definitions()

    print(f"Provider：{provider.name}")
    print(f"模型：{provider.model}")
    for round_number in range(1, rounds + 1):
        conversation.add_user(_SMOKE_REQUEST)
        environment = await build_environment_reminder(provider.name, provider.model)
        started = monotonic()
        first_text_seconds: float | None = None
        latest_usage = TokenUsage()
        text_parts: list[str] = []
        returned_tool_call = False
        stream_error = ""

        async for event in provider.stream(
            conversation.build_history(system_reminders=[environment]),
            tools,
        ):
            if event.kind == StreamEventKind.TEXT_DELTA:
                if first_text_seconds is None:
                    first_text_seconds = monotonic() - started
                text_parts.append(event.text)
            elif event.kind == StreamEventKind.TOOL_CALL:
                returned_tool_call = True
            elif event.kind == StreamEventKind.USAGE and event.usage is not None:
                latest_usage = event.usage
            elif event.kind == StreamEventKind.ERROR:
                stream_error = event.text or "Provider 流错误"

        if stream_error:
            print(f"第 {round_number} 轮失败：{stream_error}", file=sys.stderr)
            return 1
        if returned_tool_call:
            print(
                f"第 {round_number} 轮失败：模型返回了工具调用，无法保持合法 smoke 历史",
                file=sys.stderr,
            )
            return 1

        conversation.add_assistant("".join(text_parts))
        latency = (
            "unknown"
            if first_text_seconds is None
            else f"{first_text_seconds * 1000:.1f}ms"
        )
        print(
            f"第 {round_number} 轮："
            f"input={_display_count(latest_usage.input_tokens)} "
            f"output={_display_count(latest_usage.output_tokens)} "
            f"cache_read={_display_count(latest_usage.cache_read_tokens)} "
            f"cache_write={_display_count(latest_usage.cache_write_tokens)} "
            f"first_text={latency}"
        )
    return 0


def main() -> int:
    args = _parse_args()
    if args.rounds < 2:
        print("错误：--rounds 至少为 2", file=sys.stderr)
        return 2
    try:
        app_config = load_config(args.config)
        provider = _select_provider(app_config.providers, args.provider)
        return asyncio.run(_run_smoke(provider, args.rounds))
    except MewCodeError:
        print("错误：无法安全加载 MewCode 配置", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
