from __future__ import annotations

import asyncio
import os
from pathlib import Path
import time

from mewcode.config import AppConfig, ProviderConfig
from mewcode.messages import StreamEvent
from mewcode.sandbox import SandboxState
from mewcode.sandbox.windows import WindowsSandbox
from mewcode.tools.executor import ToolExecutor
from mewcode.tools.models import ToolCall
from mewcode.tools.registry import build_default_registry
from mewcode.tui.app import MewCodeApp
from mewcode.tui.screens import ToolApprovalScreen
from mewcode.tui.widgets import AssistantMessage, ErrorMessage, PromptInput, ToolMessage


class ScriptedProvider:
    name = "native-scripted"
    model = "native-scripted-model"

    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        self._responses = iter(responses)

    async def stream(self, messages, tools=None):
        for event in next(self._responses):
            yield event


def _config() -> AppConfig:
    return AppConfig(
        [ProviderConfig("native-scripted", "anthropic", "native-model", "fake-key")]
    )


def _transaction_files() -> list[Path]:
    root = Path(os.environ["LOCALAPPDATA"]) / "MewCode" / "sandbox"
    return list(root.glob("*/transactions/*.json"))


async def _wait_for(predicate, *, timeout: float = 40.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("等待 Textual Windows 原生 E2E 状态超时")


async def test_full_tui_tool_session_uses_real_windows_sandbox(
    windows_native: WindowsSandbox,
    tmp_path: Path,
):
    workspace = tmp_path / "tui-workspace"
    workspace.mkdir()
    (workspace / ".tmp").mkdir()
    (workspace / "notes.txt").write_text("native input", encoding="utf-8")
    calls = [
        ToolCall("read", "read_file", {"path": "notes.txt"}),
        ToolCall(
            "write",
            "write_file",
            {"path": "created.txt", "content": "written by native tui"},
        ),
        ToolCall(
            "search",
            "search_code",
            {"pattern": "written by native tui", "path": "."},
        ),
        ToolCall(
            "success",
            "run_command",
            {"command": "Write-Output 'powershell-ok'"},
        ),
        ToolCall(
            "nonzero",
            "run_command",
            {"command": "[Console]::Error.WriteLine('expected failure'); exit 7"},
        ),
        ToolCall(
            "timeout",
            "run_command",
            {"command": "Start-Sleep -Seconds 10"},
        ),
    ]
    provider = ScriptedProvider(
        [
            [*(StreamEvent.tool_call(call) for call in calls), StreamEvent.done()],
            [
                StreamEvent.text_delta("Windows 工具链验收完成"),
                StreamEvent.done(),
            ],
        ]
    )
    registry = build_default_registry()
    executor = ToolExecutor(
        registry,
        windows_native,
        workspace,
        timeout=3.0,
        temp_dir=workspace / ".tmp",
    )
    app = MewCodeApp(
        _config(),
        provider=provider,
        registry=registry,
        executor=executor,
        sandbox=windows_native,
    )

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.query_one(PromptInput).post_message(
            PromptInput.Submitted("执行完整 Windows 工具链")
        )
        await _wait_for(
            lambda: not app._busy and len(list(app.query(ToolMessage))) == len(calls),
            timeout=40,
        )
        await pilot.pause()

        rendered_tools = [str(message.render()) for message in app.query(ToolMessage)]
        assert any("读取 notes.txt" in text for text in rendered_tools)
        assert any("已写入" in text for text in rendered_tools)
        assert any("找到 1 处匹配" in text for text in rendered_tools)
        assert any("命令退出码 0" in text for text in rendered_tools)
        assert any("命令退出码 7" in text for text in rendered_tools)
        assert any("超时" in text for text in rendered_tools)
        assert list(app.query(AssistantMessage))[-1].text == "Windows 工具链验收完成"
        assert app.query_one(PromptInput).disabled is False
        exited: dict[str, bool] = {}
        app.exit = lambda *args, **kwargs: exited.update(done=True)
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert exited == {"done": True}

    assert (workspace / "created.txt").read_text(encoding="utf-8") == (
        "written by native tui"
    )
    assert _transaction_files() == []
    assert windows_native.diagnose().state is SandboxState.READY


async def test_tui_once_approval_prompts_again_and_can_be_denied(
    windows_native: WindowsSandbox,
    tmp_path: Path,
):
    workspace = tmp_path / "approval-workspace"
    external = tmp_path / "approval-external.txt"
    workspace.mkdir()
    (workspace / ".tmp").mkdir()
    external.write_text("approved sentinel", encoding="utf-8")
    first = ToolCall("approval-once", "read_file", {"path": str(external)})
    second = ToolCall("approval-deny", "read_file", {"path": str(external)})
    provider = ScriptedProvider(
        [
            [StreamEvent.tool_call(first), StreamEvent.done()],
            [StreamEvent.text_delta("首次读取完成"), StreamEvent.done()],
            [StreamEvent.tool_call(second), StreamEvent.done()],
            [StreamEvent.text_delta("拒绝后仍可继续"), StreamEvent.done()],
        ]
    )
    registry = build_default_registry()
    app = MewCodeApp(
        _config(),
        provider=provider,
        registry=registry,
        sandbox=windows_native,
    )
    app._executor = ToolExecutor(
        registry,
        windows_native,
        workspace,
        approve=app._approve_external_path,
        timeout=5.0,
        temp_dir=workspace / ".tmp",
    )

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.query_one(PromptInput).post_message(PromptInput.Submitted("读取外部测试文件"))
        await _wait_for(lambda: isinstance(app.screen, ToolApprovalScreen), timeout=10)
        await pilot.press("down")
        await pilot.press("enter")
        await _wait_for(lambda: not app._busy, timeout=20)
        assert list(app.query(AssistantMessage))[-1].text == "首次读取完成"

        app.query_one(PromptInput).post_message(PromptInput.Submitted("再次读取同一文件"))
        await _wait_for(lambda: isinstance(app.screen, ToolApprovalScreen), timeout=10)
        await pilot.press("enter")
        await _wait_for(lambda: not app._busy, timeout=10)
        await pilot.pause()

        rendered_errors = [str(message.render()) for message in app.query(ErrorMessage)]
        assert any("用户拒绝访问" in text for text in rendered_errors)
        assert list(app.query(AssistantMessage))[-1].text == "拒绝后仍可继续"
        assert app.query_one(PromptInput).disabled is False

    assert external.read_text(encoding="utf-8") == "approved sentinel"
    assert _transaction_files() == []


async def test_tui_cancel_real_command_recovers_and_accepts_next_conversation(
    windows_native: WindowsSandbox,
    tmp_path: Path,
):
    workspace = tmp_path / "cancel-tui-workspace"
    workspace.mkdir()
    (workspace / ".tmp").mkdir()
    pid_file = workspace / "tui-cancel.pid"
    command = (
        "[IO.File]::WriteAllText((Join-Path ([Environment]::CurrentDirectory) "
        "'tui-cancel.pid'), [string]$PID); Start-Sleep -Seconds 30"
    )
    call = ToolCall("tui-cancel", "run_command", {"command": command})
    provider = ScriptedProvider(
        [
            [StreamEvent.tool_call(call), StreamEvent.done()],
            [StreamEvent.text_delta("取消后对话恢复"), StreamEvent.done()],
        ]
    )
    registry = build_default_registry()
    executor = ToolExecutor(
        registry,
        windows_native,
        workspace,
        timeout=30.0,
        temp_dir=workspace / ".tmp",
    )
    app = MewCodeApp(
        _config(),
        provider=provider,
        registry=registry,
        executor=executor,
        sandbox=windows_native,
    )

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.query_one(PromptInput).post_message(PromptInput.Submitted("运行并取消"))
        await _wait_for(lambda: pid_file.is_file(), timeout=15)
        await pilot.press("escape")
        await _wait_for(lambda: not app._busy, timeout=15)
        assert any("取消" in str(message.render()) for message in app.query(ErrorMessage))
        assert app.query_one(PromptInput).disabled is False
        assert _transaction_files() == []

        app.query_one(PromptInput).post_message(PromptInput.Submitted("继续纯对话"))
        await _wait_for(
            lambda: not app._busy
            and bool(list(app.query(AssistantMessage)))
            and list(app.query(AssistantMessage))[-1].text == "取消后对话恢复",
            timeout=10,
        )
        assert app.is_running

    assert windows_native.diagnose().state is SandboxState.READY
