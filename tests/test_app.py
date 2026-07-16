from mewcode.config import AppConfig, ProviderConfig
import asyncio

from mewcode.messages import StreamEvent, TokenUsage
from mewcode.sandbox import SandboxDiagnostic, SandboxState
from mewcode.tools.models import ToolCall, ToolExecutionEvent, ToolResult
from mewcode.tui.app import MewCodeApp
from mewcode.tui.widgets import (
    AssistantMessage,
    ErrorMessage,
    PromptInput,
    ReadyLine,
    ToolMessage,
)
from textual.containers import VerticalScroll


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, events):
        self._events = events

    async def stream(self, messages, tools=None):
        for e in self._events:
            yield e


class ScriptedProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, responses):
        self._responses = iter(responses)

    async def stream(self, messages, tools=None):
        for event in next(self._responses):
            yield event


class FakeExecutor:
    async def execute_batch(self, calls):
        return [
            ToolResult(call.id, call.name, True, "", "已完成")
            for call in calls
        ]

    async def execute_stream(self, calls, cancel_requested):
        for call in calls:
            yield ToolExecutionEvent.started(call)
            result = ToolResult(call.id, call.name, True, "", "已完成")
            yield ToolExecutionEvent.finished(call, result)


class FakeSandbox:
    def __init__(self, state=SandboxState.READY, *, raises=False):
        self.state = state
        self.raises = raises

    def diagnose(self):
        if self.raises:
            raise RuntimeError("raw local diagnostic")
        return SandboxDiagnostic(
            self.state,
            "windows-appcontainer",
            self.state.value,
            "脱敏状态",
            "脱敏建议",
        )

    async def run(self, request, timeout):
        raise AssertionError("纯对话不应调用沙箱")


def _single_config():
    return AppConfig([ProviderConfig("fake", "anthropic", "fake-model", "k")])


async def test_normal_stream_and_finalize():
    provider = FakeProvider(
        [StreamEvent.text_delta("Hello"), StreamEvent.text_delta(" world"), StreamEvent.done()]
    )
    app = MewCodeApp(_single_config(), provider=provider)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(PromptInput).post_message(PromptInput.Submitted("hi"))
        await pilot.pause()
        await pilot.pause()

        assistant = app.query_one(AssistantMessage)
        assert assistant.text == "Hello world"
        # 历史含 user + assistant
        roles = [m.role.value for m in app.conversation.messages]
        assert roles == ["user", "assistant"]
        assert app.conversation.messages[1].content == "Hello world"
        # 输入恢复可用
        assert app.query_one(PromptInput).disabled is False


async def test_error_event_does_not_crash():
    provider = FakeProvider([StreamEvent.error("boom")])
    app = MewCodeApp(_single_config(), provider=provider)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(PromptInput).post_message(PromptInput.Submitted("hi"))
        await pilot.pause()
        await pilot.pause()

        assert app.query_one(ErrorMessage) is not None
        assert app.is_running
        assert app.query_one(PromptInput).disabled is False
        # 无正文时不写入 assistant 历史
        roles = [m.role.value for m in app.conversation.messages]
        assert roles == ["user"]


async def test_exit_command():
    app = MewCodeApp(_single_config(), provider=FakeProvider([]))
    exited = {}
    async with app.run_test() as pilot:
        await pilot.pause()
        app.exit = lambda *a, **k: exited.update(done=True)
        app.query_one(PromptInput).post_message(PromptInput.Submitted("/exit"))
        await pilot.pause()
    assert exited.get("done") is True
    # /exit 不产生对话消息
    assert app.conversation.messages == []


async def test_ctrl_c_exits_app_when_input_is_focused():
    app = MewCodeApp(_single_config(), provider=FakeProvider([]))
    exited = {}
    async with app.run_test() as pilot:
        await pilot.pause()
        app.exit = lambda *a, **k: exited.update(done=True)
        await pilot.press("ctrl+c")
        await pilot.pause()
    assert exited.get("done") is True


async def test_conversation_history_expands_and_can_scroll():
    app = MewCodeApp(_single_config(), provider=FakeProvider([]))
    async with app.run_test(size=(80, 20)) as pilot:
        area = app.query_one("#conversation", VerticalScroll)
        for _ in range(16):
            message = AssistantMessage()
            await area.mount(message)
            message.append("历史回复")
            message.finalize()

        await pilot.pause()
        assert area.virtual_size.height > area.size.height
        area.scroll_end(immediate=True, animate=False)
        assert area.scroll_y > 0


async def test_follow_up_tool_calls_continue_until_final_text():
    first_call = ToolCall("first", "find_files", {"pattern": "*"})
    extra_call = ToolCall("extra", "read_file", {"path": "a.txt"})
    provider = ScriptedProvider([
        [StreamEvent.tool_call(first_call), StreamEvent.done()],
        [StreamEvent.tool_call(extra_call), StreamEvent.done()],
        [StreamEvent.text_delta("全部完成"), StreamEvent.done()],
    ])
    app = MewCodeApp(_single_config(), provider=provider, executor=FakeExecutor())
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(PromptInput).post_message(PromptInput.Submitted("列出文件"))
        for _ in range(4):
            await pilot.pause()

        assert len(list(app.query(ToolMessage))) == 2
        assert not any(
            "单轮工具调用上限" in str(message.render())
            for message in app.query(ErrorMessage)
        )
        assert list(app.query(AssistantMessage))[-1].text == "全部完成"


async def test_tool_line_keeps_tool_name_after_widget_is_mounted():
    call = ToolCall("first", "read_file", {"path": "pyproject.toml"})
    provider = ScriptedProvider([
        [StreamEvent.tool_call(call), StreamEvent.done()],
        [StreamEvent.text_delta("已读取"), StreamEvent.done()],
    ])
    app = MewCodeApp(_single_config(), provider=provider, executor=FakeExecutor())
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(PromptInput).post_message(PromptInput.Submitted("读取配置"))
        for _ in range(4):
            await pilot.pause()

        tool_line = app.query_one(ToolMessage)
        assert "read_file(pyproject.toml)" in str(tool_line.render())


async def test_usage_updates_status_bar():
    provider = FakeProvider(
        [
            StreamEvent.token_usage(TokenUsage(12, 4)),
            StreamEvent.text_delta("完成"),
            StreamEvent.done(),
        ]
    )
    app = MewCodeApp(_single_config(), provider=provider)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(PromptInput).post_message(PromptInput.Submitted("hi"))
        await pilot.pause()
        await pilot.pause()

        assert "tokens in 12 / out 4" in str(app.query_one(".status-right").render())


class BlockingProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self):
        self.started = asyncio.Event()

    async def stream(self, messages, tools=None):
        self.started.set()
        await asyncio.Event().wait()
        yield StreamEvent.done()


async def test_iteration_visible_and_escape_cancels_current_task():
    provider = BlockingProvider()
    app = MewCodeApp(_single_config(), provider=provider)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(PromptInput).post_message(PromptInput.Submitted("等待"))
        await provider.started.wait()
        await pilot.pause()
        indicator = app.query_one(".thinking")
        assert "第 1 轮" in str(indicator.render())

        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()

        assert app.query_one(PromptInput).disabled is False
        assert any("取消" in str(message.render()) for message in app.query(ErrorMessage))
        assert app.is_running


async def test_sandbox_status_is_loaded_without_blocking_tui():
    app = MewCodeApp(
        _single_config(),
        provider=FakeProvider([]),
        sandbox=FakeSandbox(SandboxState.SETUP_REQUIRED),
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        assert "工具沙箱待准备" in str(app.query_one(ReadyLine).render())
        assert "windows-appcontainer/setup_required" in str(
            app.query_one(".status-left").render()
        )


async def test_broken_sandbox_keeps_pure_conversation_available():
    provider = FakeProvider([StreamEvent.text_delta("仍可回答"), StreamEvent.done()])
    app = MewCodeApp(
        _single_config(),
        provider=provider,
        sandbox=FakeSandbox(SandboxState.BROKEN),
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(PromptInput).post_message(PromptInput.Submitted("只聊天"))
        await pilot.pause()
        await pilot.pause()
        assert app.query_one(AssistantMessage).text == "仍可回答"
        assert "工具沙箱不可用" in str(app.query_one(ReadyLine).render())


async def test_diagnostic_exception_is_sanitized():
    app = MewCodeApp(
        _single_config(), provider=FakeProvider([]), sandbox=FakeSandbox(raises=True)
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        rendered = str(app.query_one(ReadyLine).render())
        assert "工具沙箱不可用" in rendered
        assert "raw local diagnostic" not in rendered
