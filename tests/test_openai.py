import json

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
from mewcode.providers.openai import OpenAIProvider, _serialize
from mewcode.tools.models import ToolCall, ToolResult

SSE = (
    'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
    "data: [DONE]\n\n"
)


@respx.mock
async def test_openai_stream_text_deltas():
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=SSE)
    )
    cfg = ProviderConfig(name="o", protocol="openai", model="gpt", api_key="k")
    prov = OpenAIProvider(cfg)
    msgs = [Message(Role.SYSTEM, "sys"), Message(Role.USER, "hi")]

    events = [ev async for ev in prov.stream(msgs)]
    kinds = [e.kind for e in events]
    texts = "".join(
        e.text for e in events if e.kind == StreamEventKind.TEXT_DELTA
    )

    assert kinds == [
        StreamEventKind.TEXT_DELTA,
        StreamEventKind.TEXT_DELTA,
        StreamEventKind.DONE,
    ]
    assert texts == "Hello world"


REASONER_SSE = (
    'data: {"choices":[{"delta":{"reasoning_content":"SECRET thinking"}}]}\n\n'
    'data: {"choices":[{"delta":{"reasoning_content":" more secret"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"Answer"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":" is 4"}}]}\n\n'
    "data: [DONE]\n\n"
)


@respx.mock
async def test_openai_drops_reasoning_content():
    """DeepSeek reasoner 等推理模型的思考内容（reasoning_content）应被丢弃。"""
    respx.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(200, text=REASONER_SSE)
    )
    cfg = ProviderConfig(
        name="ds",
        protocol="openai",
        model="deepseek-reasoner",
        api_key="k",
        base_url="https://api.deepseek.com",
    )
    prov = OpenAIProvider(cfg)
    events = [ev async for ev in prov.stream([Message(Role.USER, "1+3?")])]
    texts = "".join(
        e.text for e in events if e.kind == StreamEventKind.TEXT_DELTA
    )
    assert texts == "Answer is 4"
    # 思考内容绝不泄漏
    assert not any("SECRET" in e.text or "secret" in e.text for e in events)
    assert events[-1].kind == StreamEventKind.DONE


@respx.mock
async def test_openai_custom_base_url():
    route = respx.post("https://proxy.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, text="data: [DONE]\n\n")
    )
    cfg = ProviderConfig(
        name="o",
        protocol="openai",
        model="gpt",
        api_key="k",
        base_url="https://proxy.local/v1",
    )
    prov = OpenAIProvider(cfg)
    events = [ev async for ev in prov.stream([Message(Role.USER, "hi")])]
    assert route.called
    assert events[-1].kind == StreamEventKind.DONE


@respx.mock
async def test_openai_assembles_fragmented_tool_call_name_and_arguments():
    """兼容端点可以把工具名和参数拆在不同 SSE 分片中。"""
    sse = (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"run_","arguments":"{\\"command\\":\\"ls"}}]}}]}\n\n'
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"command","arguments":" -la\\"}"}}]}}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=sse)
    )
    prov = OpenAIProvider(ProviderConfig(name="o", protocol="openai", model="gpt", api_key="k"))

    events = [ev async for ev in prov.stream([Message(Role.USER, "列出文件")])]

    calls = [ev.call for ev in events if ev.kind == StreamEventKind.TOOL_CALL]
    assert len(calls) == 1
    assert calls[0] is not None
    assert calls[0].name == "run_command"
    assert calls[0].arguments == {"command": "ls -la"}


@respx.mock
async def test_openai_rejects_tool_call_without_name():
    sse = (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"arguments":"{}"}}]}}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=sse)
    )
    prov = OpenAIProvider(ProviderConfig(name="o", protocol="openai", model="gpt", api_key="k"))

    events = [ev async for ev in prov.stream([Message(Role.USER, "列出文件")])]

    assert not any(ev.kind == StreamEventKind.TOOL_CALL for ev in events)
    assert any(ev.text == "工具调用缺少工具名称，已忽略" for ev in events)


@respx.mock
async def test_openai_uses_top_level_name_when_nested_name_is_null():
    """部分 DeepSeek 兼容响应会将 function.name 置空而平铺实际名称。"""
    sse = (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","name":"read_file","function":{"name":null,"arguments":"{\\"path\\":\\"pyproject.toml\\"}"}}]}}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=sse)
    )
    prov = OpenAIProvider(ProviderConfig(name="o", protocol="openai", model="gpt", api_key="k"))

    events = [ev async for ev in prov.stream([Message(Role.USER, "读取配置")])]

    calls = [ev.call for ev in events if ev.kind == StreamEventKind.TOOL_CALL]
    assert calls[0] is not None
    assert calls[0].name == "read_file"


def test_openai_serializes_preamble_and_tool_calls_as_one_assistant_message():
    call = ToolCall("call-1", "read_file", {"path": "a.txt"})
    history = [
        ConversationItem.text_item(ConversationItemKind.SYSTEM, "sys"),
        ConversationItem.text_item(ConversationItemKind.USER, "读"),
        ConversationItem.assistant("我先读取。", [call]),
        ConversationItem.tool_results(
            [ToolResult("call-1", "read_file", True, "ok", "完成")]
        ),
    ]

    messages = _serialize(history)
    assert messages[2]["role"] == "assistant"
    assert messages[2]["content"] == "我先读取。"
    assert messages[2]["tool_calls"][0]["id"] == "call-1"
    assert messages[3]["role"] == "tool"


@respx.mock
async def test_openai_stream_usage_snapshot_and_request_option():
    sse = (
        'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":5}}\n\n'
        "data: [DONE]\n\n"
    )
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=sse)
    )
    prov = OpenAIProvider(
        ProviderConfig(name="o", protocol="openai", model="gpt", api_key="k")
    )

    events = [ev async for ev in prov.stream([Message(Role.USER, "hi")])]

    usage = [ev.usage for ev in events if ev.kind == StreamEventKind.USAGE]
    assert usage[0] is not None
    assert usage[0].input_tokens == 12
    assert usage[0].output_tokens == 5
    request_body = json.loads(route.calls.last.request.content)
    assert request_body["stream_options"] == {"include_usage": True}


@respx.mock
async def test_openai_missing_usage_fields_stay_unknown():
    sse = 'data: {"choices":[],"usage":{"prompt_tokens":true}}\n\ndata: [DONE]\n\n'
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=sse)
    )
    prov = OpenAIProvider(
        ProviderConfig(name="o", protocol="openai", model="gpt", api_key="k")
    )

    events = [ev async for ev in prov.stream([Message(Role.USER, "hi")])]
    usage = next(ev.usage for ev in events if ev.kind == StreamEventKind.USAGE)
    assert usage is not None
    assert usage.input_tokens is None
    assert usage.output_tokens is None
