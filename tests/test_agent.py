import asyncio
import json

import httpx
import respx

from mewcode.agent import AgentLoop
from mewcode.config import ProviderConfig
from mewcode.messages import (
    AgentEventKind,
    StopReason,
    StreamEvent,
    TokenUsage,
)
from mewcode.providers.openai import OpenAIProvider
from mewcode.tools.models import ToolCall, ToolExecutionEvent, ToolResult
from mewcode.tools.registry import build_default_registry


class ScriptedProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    async def stream(self, history, tools=None):
        self.requests.append((history, tools or []))
        for event in next(self.responses):
            yield event


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls = []

    async def execute_stream(self, calls, cancel_requested):
        self.calls.extend(calls)
        for call in calls:
            yield ToolExecutionEvent.started(call)
            if cancel_requested.is_set():
                result = ToolResult.failure(call, "cancelled", "工具调用已取消")
            elif call.name == "hallucinated":
                result = ToolResult.failure(
                    call, "unknown_tool", f"未登记的工具：{call.name}"
                )
            else:
                result = ToolResult(call.id, call.name, True, "ok", "已完成")
            yield ToolExecutionEvent.finished(call, result)


async def _collect(agent: AgentLoop, text: str):
    return [event async for event in agent.run(text)]


def _agent(responses, *, max_iterations=20, executor=None):
    provider = ScriptedProvider(responses)
    executor = executor or RecordingExecutor()
    agent = AgentLoop(
        provider,
        build_default_registry(),
        executor,
        max_iterations=max_iterations,
    )
    return agent, provider, executor


async def test_natural_completion_emits_text_and_completed():
    agent, _, _ = _agent(
        [[StreamEvent.text_delta("完成"), StreamEvent.done()]]
    )

    events = await _collect(agent, "你好")

    assert [event.kind for event in events] == [
        AgentEventKind.USER_MESSAGE,
        AgentEventKind.ITERATION_STARTED,
        AgentEventKind.TEXT_DELTA,
        AgentEventKind.ROUND_FINISHED,
        AgentEventKind.TASK_FINISHED,
    ]
    assert events[-1].stop_reason == StopReason.COMPLETED
    assert agent.conversation.items[-1].text == "完成"


async def test_environment_and_plan_are_temporary_system_reminders(monkeypatch):
    marker = "<system-reminder>环境标记</system-reminder>"

    async def environment(*args, **kwargs):
        return marker

    monkeypatch.setattr("mewcode.agent.build_environment_reminder", environment)
    agent, provider, _ = _agent(
        [
            [StreamEvent.text_delta("完成"), StreamEvent.done()],
            [StreamEvent.text_delta("计划完成"), StreamEvent.done()],
        ]
    )

    await _collect(agent, "普通任务")
    await _collect(agent, "/plan 计划任务")

    normal_history = provider.requests[0][0]
    plan_history = provider.requests[1][0]
    assert normal_history[0].text == plan_history[0].text
    assert [item.text for item in normal_history if item.text == marker] == [marker]
    assert [item.text for item in plan_history if item.text == marker] == [marker]
    assert not any("当前处于计划模式" in item.text for item in normal_history)
    assert any("当前处于计划模式" in item.text for item in plan_history)
    assert all(
        item.kind.value != "system_reminder"
        for item in agent.conversation.items
    )


async def test_multiround_tool_calls_continue_until_plain_text():
    read = ToolCall("1", "read_file", {"path": "a"})
    write = ToolCall("2", "write_file", {"path": "b", "content": "x"})
    agent, provider, executor = _agent(
        [
            [StreamEvent.text_delta("先读。"), StreamEvent.tool_call(read), StreamEvent.done()],
            [StreamEvent.tool_call(write), StreamEvent.done()],
            [StreamEvent.text_delta("全部完成"), StreamEvent.done()],
        ]
    )

    events = await _collect(agent, "完成任务")

    assert len(provider.requests) == 3
    assert [call.id for call in executor.calls] == ["1", "2"]
    assert events[-1].stop_reason == StopReason.COMPLETED
    assert [event.iteration for event in events if event.kind == AgentEventKind.ITERATION_STARTED] == [1, 2, 3]
    assistant = agent.conversation.items[1]
    assert assistant.text == "先读。"
    assert assistant.calls == (read,)


async def test_reasoning_is_hidden_and_kept_for_tool_call_continuation():
    read = ToolCall("1", "read_file", {"path": "a"})
    write = ToolCall("2", "write_file", {"path": "b", "content": "x"})
    agent, provider, _ = _agent(
        [
            [
                StreamEvent.reasoning_delta("SECRET-FIRST"),
                StreamEvent.text_delta("先读。"),
                StreamEvent.tool_call(read),
                StreamEvent.done(),
            ],
            [
                StreamEvent.reasoning_delta("SECRET-SECOND"),
                StreamEvent.tool_call(write),
                StreamEvent.done(),
            ],
            [
                StreamEvent.reasoning_delta("SECRET-FINAL"),
                StreamEvent.text_delta("全部完成"),
                StreamEvent.done(),
            ],
        ]
    )

    events = await _collect(agent, "完成任务")

    visible = "\n".join(event.text for event in events)
    assert "SECRET" not in visible
    assert events[-1].stop_reason == StopReason.COMPLETED
    assistant_items = [item for item in agent.conversation.items if item.calls]
    assert [item.reasoning_content for item in assistant_items] == [
        "SECRET-FIRST",
        "SECRET-SECOND",
    ]
    assert all(
        "SECRET-FINAL" not in item.reasoning_content
        for item in agent.conversation.items
    )
    assert not any(
        "SECRET" in message.content
        for message in agent.conversation.messages
    )
    second_request_history = provider.requests[1][0]
    assert any(
        item.reasoning_content == "SECRET-FIRST"
        for item in second_request_history
    )


async def test_reasoning_without_tool_call_is_not_saved():
    agent, _, _ = _agent(
        [
            [
                StreamEvent.reasoning_delta("SECRET-FINAL"),
                StreamEvent.text_delta("完成"),
                StreamEvent.done(),
            ]
        ]
    )

    events = await _collect(agent, "回答")

    assert "SECRET" not in "\n".join(event.text for event in events)
    assert agent.conversation.items[-1].text == "完成"
    assert agent.conversation.items[-1].reasoning_content == ""


@respx.mock
async def test_openai_reasoning_round_trips_through_agent_tool_loop():
    first = (
        'data: {"choices":[{"delta":{"reasoning_content":"SECRET-ROUND"}}]}\n\n'
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"read_file","arguments":"{\\"path\\":\\"README.md\\"}"}}]}}]}\n\n'
        "data: [DONE]\n\n"
    )
    second = (
        'data: {"choices":[{"delta":{"content":"读取完成"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    route = respx.post("https://compatible.local/chat/completions").mock(
        side_effect=[
            httpx.Response(200, text=first),
            httpx.Response(200, text=second),
        ]
    )
    provider = OpenAIProvider(
        ProviderConfig(
            name="compatible",
            protocol="openai",
            model="vendor-reasoner",
            api_key="k",
            base_url="https://compatible.local",
            thinking=True,
        )
    )
    agent = AgentLoop(
        provider,
        build_default_registry(),
        RecordingExecutor(),
    )

    events = await _collect(agent, "读取项目说明")

    assert events[-1].stop_reason == StopReason.COMPLETED
    assert "SECRET" not in "\n".join(event.text for event in events)
    assert route.call_count == 2
    second_body = json.loads(route.calls[1].request.content)
    assistant = next(
        message
        for message in second_body["messages"]
        if message["role"] == "assistant" and message.get("tool_calls")
    )
    assert second_body["thinking"] == {"type": "enabled"}
    assert assistant["content"] == ""
    assert assistant["reasoning_content"] == "SECRET-ROUND"
    tool_index = second_body["messages"].index(assistant) + 1
    assert second_body["messages"][tool_index]["role"] == "tool"


async def test_iteration_limit_stops_without_extra_provider_request():
    call = ToolCall("1", "read_file", {"path": "a"})
    agent, provider, _ = _agent(
        [
            [
                StreamEvent.reasoning_delta("SECRET-LIMIT-1"),
                StreamEvent.tool_call(call),
                StreamEvent.done(),
            ],
            [
                StreamEvent.reasoning_delta("SECRET-LIMIT-2"),
                StreamEvent.tool_call(call),
                StreamEvent.done(),
            ],
        ],
        max_iterations=2,
    )

    events = await _collect(agent, "循环")

    assert len(provider.requests) == 2
    assert events[-1].stop_reason == StopReason.ITERATION_LIMIT
    assert [
        item.reasoning_content
        for item in agent.conversation.items
        if item.calls
    ] == ["SECRET-LIMIT-1", "SECRET-LIMIT-2"]
    assert "SECRET" not in "\n".join(event.text for event in events)


async def test_stream_error_stops_and_next_task_can_run():
    agent, provider, _ = _agent(
        [
            [StreamEvent.text_delta("部分"), StreamEvent.error("boom")],
            [StreamEvent.text_delta("恢复"), StreamEvent.done()],
        ]
    )

    first = await _collect(agent, "第一次")
    second = await _collect(agent, "第二次")

    assert first[-1].stop_reason == StopReason.STREAM_ERROR
    assert second[-1].stop_reason == StopReason.COMPLETED
    assert len(provider.requests) == 2


async def test_three_unknown_only_rounds_stop():
    unknown = ToolCall("x", "hallucinated", {})
    agent, provider, _ = _agent(
        [[StreamEvent.tool_call(unknown), StreamEvent.done()] for _ in range(3)]
    )

    events = await _collect(agent, "未知")

    assert len(provider.requests) == 3
    assert events[-1].stop_reason == StopReason.UNKNOWN_TOOL_LIMIT
    assert all(
        item.results[0].error and item.results[0].error.code == "unknown_tool"
        for item in agent.conversation.items
        if item.results
    )


async def test_known_tool_resets_unknown_round_counter():
    unknown = ToolCall("x", "hallucinated", {})
    known = ToolCall("k", "read_file", {"path": "a"})
    responses = [
        [StreamEvent.tool_call(unknown), StreamEvent.done()],
        [StreamEvent.tool_call(unknown), StreamEvent.done()],
        [StreamEvent.tool_call(known), StreamEvent.done()],
        [StreamEvent.tool_call(unknown), StreamEvent.done()],
        [StreamEvent.tool_call(unknown), StreamEvent.done()],
        [StreamEvent.text_delta("完成"), StreamEvent.done()],
    ]
    agent, provider, _ = _agent(responses)

    events = await _collect(agent, "重置")

    assert len(provider.requests) == 6
    assert events[-1].stop_reason == StopReason.COMPLETED


async def test_usage_snapshots_are_not_double_counted_across_rounds():
    call = ToolCall("1", "read_file", {"path": "a"})
    agent, _, _ = _agent(
        [
            [
                StreamEvent.token_usage(TokenUsage(10, 0, 3, 5)),
                StreamEvent.token_usage(TokenUsage(10, 2, 4, 5)),
                StreamEvent.tool_call(call),
                StreamEvent.done(),
            ],
            [
                StreamEvent.token_usage(TokenUsage(12, 5, 6, None)),
                StreamEvent.text_delta("完成"),
                StreamEvent.done(),
            ],
        ]
    )

    events = await _collect(agent, "统计")
    usage = [event.usage for event in events if event.kind == AgentEventKind.USAGE_UPDATED]

    assert usage[-1] == TokenUsage(22, 7, 10, 5)


async def test_plan_full_reminder_repeats_on_rounds_1_6_11_16(monkeypatch):
    async def environment(*args, **kwargs):
        return "<system-reminder>环境</system-reminder>"

    monkeypatch.setattr("mewcode.agent.build_environment_reminder", environment)
    calls = [
        ToolCall(str(index), "read_file", {"path": "a"})
        for index in range(1, 18)
    ]
    agent, provider, _ = _agent(
        [
            [StreamEvent.tool_call(call), StreamEvent.done()]
            for call in calls
        ],
        max_iterations=17,
    )

    events = await _collect(agent, "/plan 调查直到上限")

    full_rounds = []
    for index, (history, _) in enumerate(provider.requests, start=1):
        reminders = [
            item.text
            for item in history
            if item.kind.value == "system_reminder"
        ]
        assert len(reminders) == 2
        if "具体、可执行且可验证" in reminders[1]:
            full_rounds.append(index)
    assert full_rounds == [1, 6, 11, 16]
    assert events[-1].stop_reason == StopReason.ITERATION_LIMIT


async def test_each_new_plan_task_restarts_with_full_reminder(monkeypatch):
    async def environment(*args, **kwargs):
        return "<system-reminder>环境</system-reminder>"

    monkeypatch.setattr("mewcode.agent.build_environment_reminder", environment)
    agent, provider, _ = _agent(
        [
            [StreamEvent.text_delta("计划一"), StreamEvent.done()],
            [StreamEvent.text_delta("计划二"), StreamEvent.done()],
        ]
    )

    await _collect(agent, "/plan 第一个任务")
    await _collect(agent, "/plan 第二个任务")

    for history, _ in provider.requests:
        plan_reminder = next(
            item.text
            for item in history
            if item.kind.value == "system_reminder" and "计划模式" in item.text
        )
        assert "具体、可执行且可验证" in plan_reminder


async def test_plan_uses_read_only_tools_and_do_uses_all_tools():
    read = ToolCall("1", "read_file", {"path": "a"})
    write = ToolCall("2", "write_file", {"path": "b", "content": "x"})
    agent, provider, executor = _agent(
        [
            [StreamEvent.tool_call(read), StreamEvent.done()],
            [StreamEvent.text_delta("计划：写入 b"), StreamEvent.done()],
            [StreamEvent.tool_call(write), StreamEvent.done()],
            [StreamEvent.text_delta("执行完成"), StreamEvent.done()],
        ]
    )

    plan_events = await _collect(agent, "/plan 检查并规划")
    do_events = await _collect(agent, "/do")

    assert plan_events[-1].stop_reason == StopReason.COMPLETED
    assert do_events[-1].stop_reason == StopReason.COMPLETED
    assert agent.pending_plan == "计划：写入 b"
    assert [[tool.name for tool in request[1]] for request in provider.requests[:2]] == [
        ["read_file", "find_files", "search_code"],
        ["read_file", "find_files", "search_code"],
    ]
    assert len(provider.requests[2][1]) == 6
    assert [call.id for call in executor.calls] == ["1", "2"]


async def test_plan_blocks_side_effect_even_if_model_requests_it():
    write = ToolCall("1", "write_file", {"path": "b", "content": "x"})
    agent, _, executor = _agent(
        [
            [StreamEvent.tool_call(write), StreamEvent.done()],
            [StreamEvent.text_delta("计划完成"), StreamEvent.done()],
        ]
    )

    events = await _collect(agent, "/plan 只做计划")
    results = [event.result for event in events if event.kind == AgentEventKind.TOOL_FINISHED]

    assert executor.calls == []
    assert results[0] is not None
    assert results[0].error is not None
    assert results[0].error.code == "tool_not_allowed_in_plan"


async def test_do_without_saved_plan_does_not_call_provider():
    agent, provider, _ = _agent([])

    events = await _collect(agent, "/do")

    assert provider.requests == []
    assert events[-1].stop_reason == StopReason.STREAM_ERROR
    assert any("没有可执行的计划" in event.text for event in events)


class BlockingProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def stream(self, history, tools=None):
        self.started.set()
        await asyncio.Event().wait()
        yield StreamEvent.done()


class BlockingReasoningProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        self.waiting = asyncio.Event()

    async def stream(self, history, tools=None):
        yield StreamEvent.reasoning_delta("SECRET-PARTIAL")
        self.waiting.set()
        await asyncio.Event().wait()


async def test_cancel_during_provider_stream_restores_agent():
    provider = BlockingProvider()
    agent = AgentLoop(
        provider,
        build_default_registry(),
        RecordingExecutor(),
    )
    task = asyncio.create_task(_collect(agent, "等待"))
    await provider.started.wait()

    assert agent.cancel_current() is True
    events = await asyncio.wait_for(task, timeout=1)

    assert events[-1].stop_reason == StopReason.CANCELLED
    assert agent.is_running is False
    assert agent.cancel_current() is False


async def test_cancel_after_partial_reasoning_leaves_no_orphan_history():
    provider = BlockingReasoningProvider()
    agent = AgentLoop(
        provider,
        build_default_registry(),
        RecordingExecutor(),
    )
    task = asyncio.create_task(_collect(agent, "等待"))
    await provider.waiting.wait()

    assert agent.cancel_current() is True
    events = await asyncio.wait_for(task, timeout=1)

    assert events[-1].stop_reason == StopReason.CANCELLED
    assert not any(item.reasoning_content for item in agent.conversation.items)
    assert "SECRET" not in "\n".join(event.text for event in events)


@respx.mock
async def test_openai_http_error_then_next_task_recovers():
    route = respx.post("https://compatible.local/chat/completions").mock(
        side_effect=[
            httpx.Response(
                400,
                json={
                    "error": {"message": "bad request"},
                    "reasoning_content": "SECRET-ERROR",
                },
            ),
            httpx.Response(
                200,
                text=(
                    'data: {"choices":[{"delta":{"content":"恢复"}}]}\n\n'
                    "data: [DONE]\n\n"
                ),
            ),
        ]
    )
    provider = OpenAIProvider(
        ProviderConfig(
            name="compatible",
            protocol="openai",
            model="vendor-reasoner",
            api_key="k",
            base_url="https://compatible.local",
            thinking=True,
        )
    )
    agent = AgentLoop(
        provider,
        build_default_registry(),
        RecordingExecutor(),
    )

    first = await _collect(agent, "第一次")
    second = await _collect(agent, "第二次")

    assert first[-1].stop_reason == StopReason.STREAM_ERROR
    assert second[-1].stop_reason == StopReason.COMPLETED
    assert route.call_count == 2
    assert "SECRET" not in "\n".join(
        event.text for event in [*first, *second]
    )


class BlockingExecutor(RecordingExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def execute_stream(self, calls, cancel_requested):
        self.calls.extend(calls)
        for call in calls:
            yield ToolExecutionEvent.started(call)
            self.started.set()
            await cancel_requested.wait()
            yield ToolExecutionEvent.finished(
                call, ToolResult.failure(call, "cancelled", "工具调用已取消")
            )


async def test_cancel_during_tool_execution_keeps_history_paired():
    call = ToolCall("1", "read_file", {"path": "a"})
    executor = BlockingExecutor()
    agent, _, _ = _agent(
        [
            [
                StreamEvent.reasoning_delta("SECRET-CANCEL"),
                StreamEvent.tool_call(call),
                StreamEvent.done(),
            ]
        ],
        executor=executor,
    )
    task = asyncio.create_task(_collect(agent, "等待工具"))
    await executor.started.wait()

    assert agent.cancel_current() is True
    events = await asyncio.wait_for(task, timeout=1)

    assert events[-1].stop_reason == StopReason.CANCELLED
    result_items = [item for item in agent.conversation.items if item.results]
    assert result_items[-1].results[0].call_id == "1"
    assert result_items[-1].results[0].error is not None
    assert result_items[-1].results[0].error.code == "cancelled"
    call_item = next(item for item in agent.conversation.items if item.calls)
    assert call_item.reasoning_content == "SECRET-CANCEL"
    assert "SECRET" not in "\n".join(event.text for event in events)


async def test_empty_plan_and_empty_task_are_recoverable_errors():
    agent, provider, _ = _agent([])

    empty = await _collect(agent, "   ")
    plan = await _collect(agent, "/plan")

    assert empty[-1].stop_reason == StopReason.STREAM_ERROR
    assert plan[-1].stop_reason == StopReason.STREAM_ERROR
    assert provider.requests == []
