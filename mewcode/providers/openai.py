"""OpenAI Chat Completions API 后端：把 SSE 归一化为统一事件。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from ..messages import (
    ConversationItem,
    ConversationItemKind,
    Message,
    StreamEvent,
    TokenUsage,
)
from ..tools.models import ToolCall, ToolDefinition, ToolResult
from .base import Provider, normalize_history
from .sse import iter_sse

DEFAULT_BASE_URL = "https://api.openai.com/v1"
_INVALID_TOOL_NAMES = {"", "none", "null"}
_SENSITIVE_ERROR_FIELDS = {
    "access_token",
    "api_key",
    "authorization",
    "reasoning_content",
}


def _usable_tool_name(value: object) -> str | None:
    """筛掉部分兼容端点用来表示缺失名称的字符串哨兵。"""
    if not isinstance(value, str):
        return None
    name = value.strip()
    return name if name.casefold() not in _INVALID_TOOL_NAMES else None


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


def _tool_output(result: ToolResult) -> str:
    payload = {"ok": result.ok, "output": result.output, "summary": result.summary}
    if result.error is not None:
        payload["error"] = {"code": result.error.code, "message": result.error.message}
    return json.dumps(payload, ensure_ascii=False)


def _token_count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _tool_calls(calls: tuple[ToolCall, ...]) -> list[dict]:
    return [
        {
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": json.dumps(call.arguments, ensure_ascii=False),
            },
        }
        for call in calls
    ]


def _serialize(history: list[ConversationItem]) -> list[dict]:
    messages: list[dict] = []
    for item in history:
        if item.kind in {ConversationItemKind.SYSTEM, ConversationItemKind.USER}:
            messages.append({"role": item.kind.value, "content": item.text})
        elif item.kind == ConversationItemKind.ASSISTANT:
            message: dict = {"role": "assistant", "content": item.text or None}
            if item.calls:
                message["tool_calls"] = _tool_calls(item.calls)
            if item.reasoning_content:
                message["reasoning_content"] = item.reasoning_content
                if item.calls and not item.text:
                    message["content"] = ""
            messages.append(message)
        elif item.kind == ConversationItemKind.TOOL_CALLS:
            messages.append(
                {"role": "assistant", "content": None, "tool_calls": _tool_calls(item.calls)}
            )
        elif item.kind == ConversationItemKind.TOOL_RESULTS:
            messages.extend({"role": "tool", "tool_call_id": result.call_id, "content": _tool_output(result)} for result in item.results)
    return messages


class OpenAIProvider(Provider):
    async def stream(
        self,
        history: list[ConversationItem] | list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        base = (self._cfg.base_url or DEFAULT_BASE_URL).rstrip("/")
        url = f"{base}/chat/completions"

        convo = _serialize(normalize_history(history))

        body = {
            "model": self._cfg.model,
            "messages": convo,
            "stream": True,
        }
        if self._cfg.thinking is not None:
            body["thinking"] = {
                "type": "enabled" if self._cfg.thinking else "disabled"
            }
        # 部分 OpenAI 兼容端点会拒绝未知的 stream_options；自定义端点
        # 仍解析其主动返回的 usage，但不强制发送该可选参数。
        if self._cfg.base_url is None:
            body["stream_options"] = {"include_usage": True}
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": tool.name, "description": tool.description, "parameters": tool.input_schema}}
                for tool in tools
            ]
        headers = {
            "Authorization": f"Bearer {self._cfg.api_key}",
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
                    async for payload in iter_sse(resp):
                        if payload == "[DONE]":
                            for index in sorted(partial_calls):
                                partial = partial_calls[index]
                                name = _usable_tool_name(partial["name"])
                                if name is None:
                                    yield StreamEvent.error("工具调用缺少工具名称，已忽略")
                                    continue
                                try:
                                    arguments = json.loads(partial["arguments"])
                                    if not isinstance(arguments, dict):
                                        raise ValueError("参数必须是对象")
                                    call_id = partial["id"] or f"tool-call-{index}"
                                    yield StreamEvent.tool_call(ToolCall(call_id, name, arguments))
                                except (KeyError, ValueError, json.JSONDecodeError) as exc:
                                    yield StreamEvent.error(f"工具参数解析失败：{exc}")
                            yield StreamEvent.done()
                            return
                        try:
                            obj = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        usage = obj.get("usage")
                        if isinstance(usage, dict):
                            yield StreamEvent.token_usage(
                                TokenUsage(
                                    _token_count(usage.get("prompt_tokens")),
                                    _token_count(usage.get("completion_tokens")),
                                )
                            )
                        choices = obj.get("choices") or []
                        if choices:
                            delta = choices[0].get("delta", {})
                            reasoning = delta.get("reasoning_content")
                            if isinstance(reasoning, str) and reasoning:
                                yield StreamEvent.reasoning_delta(reasoning)
                            content = delta.get("content")
                            if content:
                                yield StreamEvent.text_delta(content)
                            for fragment in delta.get("tool_calls") or []:
                                index = int(fragment.get("index", 0))
                                current = partial_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                                call_id = fragment.get("id")
                                if isinstance(call_id, str):
                                    current["id"] += call_id
                                function = fragment.get("function") or {}
                                # OpenAI 兼容端点通常把 name 放在 function 内；少数
                                # 兼容实现会平铺到 tool_call 上。两种形态都接受。
                                name = _usable_tool_name(function.get("name"))
                                if name is None:
                                    name = _usable_tool_name(fragment.get("name"))
                                arguments = function.get("arguments", fragment.get("arguments"))
                                if name is not None:
                                    current["name"] += name
                                if isinstance(arguments, str):
                                    current["arguments"] += arguments
        except httpx.RequestError as e:
            yield StreamEvent.error(f"网络请求失败：{e}")
