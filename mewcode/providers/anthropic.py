"""Anthropic Messages API 后端：把 SSE 归一化为统一事件，并丢弃思考增量。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from ..messages import (
    ConversationItem,
    ConversationItemKind,
    Message,
    Role,
    StreamEvent,
    TokenUsage,
)
from ..tools.models import ToolCall, ToolDefinition, ToolResult
from .base import Provider, normalize_history
from .sse import iter_sse

DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = 4096
MAX_TOKENS_THINKING = 8192
THINKING_BUDGET = 2048
_SENSITIVE_ERROR_FIELDS = {
    "access_token",
    "api_key",
    "authorization",
    "reasoning_content",
    "x-api-key",
}


def _redact_error_fields(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: (
                "[已隐藏]"
                if str(key).casefold() in _SENSITIVE_ERROR_FIELDS
                else _redact_error_fields(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_error_fields(item) for item in value]
    return value


def _http_error(resp: httpx.Response) -> str:
    raw_detail = resp.text or ""
    try:
        detail = json.dumps(
            _redact_error_fields(json.loads(raw_detail)),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except json.JSONDecodeError:
        lowered = raw_detail.casefold()
        detail = (
            "响应详情已隐藏"
            if any(field in lowered for field in _SENSITIVE_ERROR_FIELDS)
            else raw_detail
        )
    detail = detail[:300]
    return f"请求失败（HTTP {resp.status_code}）：{detail}".rstrip("：").rstrip()


def _tool_result_block(result: ToolResult) -> dict:
    content = {"ok": result.ok, "output": result.output, "summary": result.summary}
    if result.error is not None:
        content["error"] = {"code": result.error.code, "message": result.error.message}
    return {"type": "tool_result", "tool_use_id": result.call_id, "content": json.dumps(content, ensure_ascii=False), "is_error": not result.ok}


def _token_count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _assistant_content(item: ConversationItem) -> str | list[dict]:
    if not item.calls:
        return item.text
    content: list[dict] = []
    if item.text:
        content.append({"type": "text", "text": item.text})
    content.extend(
        {
            "type": "tool_use",
            "id": call.id,
            "name": call.name,
            "input": call.arguments,
        }
        for call in item.calls
    )
    return content


def _serialize(history: list[ConversationItem]) -> tuple[list[dict], list[dict]]:
    system: list[dict] = []
    messages: list[dict] = []
    for item in history:
        if item.kind == ConversationItemKind.SYSTEM:
            system.append(
                {
                    "type": "text",
                    "text": item.text,
                    "cache_control": {"type": "ephemeral"},
                }
            )
        elif item.kind == ConversationItemKind.SYSTEM_REMINDER:
            system.append({"type": "text", "text": item.text})
        elif item.kind == ConversationItemKind.USER:
            messages.append({"role": "user", "content": item.text})
        elif item.kind == ConversationItemKind.ASSISTANT:
            messages.append({"role": "assistant", "content": _assistant_content(item)})
        elif item.kind == ConversationItemKind.TOOL_CALLS:
            messages.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                for call in item.calls
            ]})
        elif item.kind == ConversationItemKind.TOOL_RESULTS:
            messages.append({"role": "user", "content": [_tool_result_block(result) for result in item.results]})
    return system, messages


def _usage_snapshot(
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read_tokens: int | None,
    cache_write_tokens: int | None,
) -> TokenUsage:
    known_input_parts = [
        value
        for value in (input_tokens, cache_read_tokens, cache_write_tokens)
        if value is not None
    ]
    total_input = sum(known_input_parts) if known_input_parts else None
    return TokenUsage(
        total_input,
        output_tokens,
        cache_read_tokens,
        cache_write_tokens,
    )


def _updated_count(current: int | None, usage: dict, key: str) -> int | None:
    return _token_count(usage[key]) if key in usage else current


class AnthropicProvider(Provider):
    async def stream(
        self,
        history: list[ConversationItem] | list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        base = (self._cfg.base_url or DEFAULT_BASE_URL).rstrip("/")
        url = f"{base}/v1/messages"

        system_blocks, convo = _serialize(normalize_history(history))

        body: dict = {
            "model": self._cfg.model,
            "max_tokens": MAX_TOKENS_THINKING if self._cfg.thinking else MAX_TOKENS,
            "messages": convo,
            "stream": True,
        }
        if system_blocks:
            body["system"] = system_blocks
        if self._cfg.thinking:
            body["thinking"] = {"type": "enabled", "budget_tokens": THINKING_BUDGET}
        if tools:
            body["tools"] = [
                {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
                for tool in tools
            ]
            body["tools"][-1]["cache_control"] = {"type": "ephemeral"}

        headers = {
            "x-api-key": self._cfg.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        await resp.aread()
                        yield StreamEvent.error(_http_error(resp))
                        return
                    partial_calls: dict[int, dict[str, str]] = {}
                    input_tokens: int | None = None
                    output_tokens: int | None = None
                    cache_read_tokens: int | None = None
                    cache_write_tokens: int | None = None
                    async for payload in iter_sse(resp):
                        try:
                            obj = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        etype = obj.get("type")
                        if etype == "message_start":
                            usage = (obj.get("message") or {}).get("usage")
                            if isinstance(usage, dict):
                                input_tokens = _token_count(usage.get("input_tokens"))
                                output_tokens = _token_count(usage.get("output_tokens"))
                                cache_read_tokens = _token_count(
                                    usage.get("cache_read_input_tokens")
                                )
                                cache_write_tokens = _token_count(
                                    usage.get("cache_creation_input_tokens")
                                )
                                yield StreamEvent.token_usage(
                                    _usage_snapshot(
                                        input_tokens,
                                        output_tokens,
                                        cache_read_tokens,
                                        cache_write_tokens,
                                    )
                                )
                        elif etype == "message_delta":
                            usage = obj.get("usage")
                            if isinstance(usage, dict):
                                input_tokens = _updated_count(
                                    input_tokens, usage, "input_tokens"
                                )
                                output_tokens = _updated_count(
                                    output_tokens, usage, "output_tokens"
                                )
                                cache_read_tokens = _updated_count(
                                    cache_read_tokens,
                                    usage,
                                    "cache_read_input_tokens",
                                )
                                cache_write_tokens = _updated_count(
                                    cache_write_tokens,
                                    usage,
                                    "cache_creation_input_tokens",
                                )
                                yield StreamEvent.token_usage(
                                    _usage_snapshot(
                                        input_tokens,
                                        output_tokens,
                                        cache_read_tokens,
                                        cache_write_tokens,
                                    )
                                )
                        if etype == "content_block_start":
                            block = obj.get("content_block") or {}
                            if block.get("type") == "tool_use":
                                partial_calls[int(obj.get("index", 0))] = {
                                    "id": block.get("id", ""),
                                    "name": block.get("name", ""),
                                    "arguments": "",
                                }
                        if etype == "content_block_delta":
                            delta = obj.get("delta", {})
                            if delta.get("type") == "text_delta":
                                yield StreamEvent.text_delta(delta.get("text", ""))
                            elif delta.get("type") == "input_json_delta":
                                partial = partial_calls.get(int(obj.get("index", 0)))
                                if partial is not None:
                                    partial["arguments"] += delta.get("partial_json", "")
                            # thinking_delta 识别后直接丢弃，不产出事件
                        elif etype == "content_block_stop":
                            partial = partial_calls.pop(int(obj.get("index", 0)), None)
                            if partial is not None:
                                try:
                                    arguments = json.loads(partial["arguments"] or "{}")
                                    if not isinstance(arguments, dict):
                                        raise ValueError("参数必须是对象")
                                    yield StreamEvent.tool_call(ToolCall(partial["id"], partial["name"], arguments))
                                except (ValueError, json.JSONDecodeError) as exc:
                                    yield StreamEvent.error(f"工具参数解析失败：{exc}")
                        elif etype == "message_stop":
                            yield StreamEvent.done()
                            return
        except httpx.RequestError as e:
            yield StreamEvent.error(f"网络请求失败：{e}")
