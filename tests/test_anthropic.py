import httpx
import respx

from mewcode.config import ProviderConfig
from mewcode.messages import (
    ConversationItem,
    ConversationItemKind,
    Message,
    Role,
    StreamEventKind,
)
from mewcode.providers.anthropic import AnthropicProvider, _serialize
from mewcode.tools.models import ToolCall, ToolResult

SSE = (
    "event: content_block_delta\n"
    'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"SECRET-REASONING"}}\n\n'
    'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}\n\n'
    'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":" world"}}\n\n'
    'data: {"type":"message_stop"}\n\n'
)


@respx.mock
async def test_anthropic_stream_drops_thinking():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, text=SSE)
    )
    cfg = ProviderConfig(
        name="a", protocol="anthropic", model="m", api_key="k", thinking=True
    )
    prov = AnthropicProvider(cfg)
    msgs = [Message(Role.SYSTEM, "sys"), Message(Role.USER, "hi")]

    events = [ev async for ev in prov.stream(msgs)]
    kinds = [e.kind for e in events]
    texts = "".join(
        e.text for e in events if e.kind == StreamEventKind.TEXT_DELTA
    )

    assert kinds[-1] == StreamEventKind.DONE
    assert texts == "Hello world"
    # 思考文本绝不泄漏到任何事件
    assert not any("SECRET" in e.text for e in events)


@respx.mock
async def test_anthropic_http_error_becomes_event():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(401, text='{"error":"unauthorized"}')
    )
    cfg = ProviderConfig(name="a", protocol="anthropic", model="m", api_key="k")
    prov = AnthropicProvider(cfg)
    events = [ev async for ev in prov.stream([Message(Role.USER, "hi")])]
    assert len(events) == 1
    assert events[0].kind == StreamEventKind.ERROR
    assert "401" in events[0].text


def test_anthropic_serializes_preamble_and_tools_in_one_assistant_turn():
    call = ToolCall("call-1", "read_file", {"path": "a.txt"})
    history = [
        ConversationItem.text_item(ConversationItemKind.SYSTEM, "sys"),
        ConversationItem.text_item(ConversationItemKind.USER, "读"),
        ConversationItem.assistant("我先读取。", [call]),
        ConversationItem.tool_results(
            [ToolResult("call-1", "read_file", True, "ok", "完成")]
        ),
    ]

    system, messages = _serialize(history)
    assert system == "sys"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"][0] == {"type": "text", "text": "我先读取。"}
    assert messages[1]["content"][1]["type"] == "tool_use"
    assert messages[2]["content"][0]["type"] == "tool_result"


@respx.mock
async def test_anthropic_stream_usage_snapshots():
    sse = (
        'data: {"type":"message_start","message":{"usage":{"input_tokens":11,"output_tokens":0}}}\n\n'
        'data: {"type":"message_delta","usage":{"output_tokens":7}}\n\n'
        'data: {"type":"message_stop"}\n\n'
    )
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, text=sse)
    )
    prov = AnthropicProvider(
        ProviderConfig(name="a", protocol="anthropic", model="m", api_key="k")
    )

    events = [ev async for ev in prov.stream([Message(Role.USER, "hi")])]
    usage = [ev.usage for ev in events if ev.kind == StreamEventKind.USAGE]
    assert [(item.input_tokens, item.output_tokens) for item in usage if item] == [
        (11, 0),
        (11, 7),
    ]


@respx.mock
async def test_anthropic_invalid_usage_stays_unknown():
    sse = (
        'data: {"type":"message_start","message":{"usage":{"input_tokens":true}}}\n\n'
        'data: {"type":"message_stop"}\n\n'
    )
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, text=sse)
    )
    prov = AnthropicProvider(
        ProviderConfig(name="a", protocol="anthropic", model="m", api_key="k")
    )

    events = [ev async for ev in prov.stream([Message(Role.USER, "hi")])]
    usage = next(ev.usage for ev in events if ev.kind == StreamEventKind.USAGE)
    assert usage is not None
    assert usage.input_tokens is None
    assert usage.output_tokens is None
