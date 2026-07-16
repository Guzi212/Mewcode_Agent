from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Static

from mewcode.config import ProviderConfig
from mewcode.sandbox import AccessMode, AccessRequest, ApprovalScope
from mewcode.tui.screens import ProviderSelectScreen, ToolApprovalScreen


class Harness(App):
    def __init__(self, providers):
        super().__init__()
        self._providers = providers
        self.selected = None

    def on_mount(self):
        self.push_screen(
            ProviderSelectScreen(self._providers),
            lambda result: setattr(self, "selected", result),
        )


async def test_select_returns_highlighted_first():
    providers = [
        ProviderConfig("a", "anthropic", "m1", "k1"),
        ProviderConfig("b", "openai", "m2", "k2"),
    ]
    app = Harness(providers)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert app.selected is not None
    assert app.selected.name == "a"


async def test_select_second_with_arrow():
    providers = [
        ProviderConfig("a", "anthropic", "m1", "k1"),
        ProviderConfig("b", "openai", "m2", "k2"),
    ]
    app = Harness(providers)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
    assert app.selected is not None
    assert app.selected.name == "b"


class ApprovalHarness(App):
    def __init__(self, request: AccessRequest):
        super().__init__()
        self.request = request
        self.selected = None

    def on_mount(self):
        self.push_screen(
            ToolApprovalScreen(self.request),
            lambda result: setattr(self, "selected", result),
        )


@pytest.mark.parametrize(
    ("down", "expected"),
    [
        (0, ApprovalScope.DENY),
        (1, ApprovalScope.ONCE),
        (2, ApprovalScope.SESSION),
    ],
)
async def test_approval_screen_displays_normalized_request_and_returns_scope(
    down: int, expected: ApprovalScope
):
    target = Path(r"D:\outside\approved.txt")
    request = AccessRequest(
        "run_command",
        target,
        AccessMode.EXECUTE,
        "Get-Content approved.txt",
    )
    app = ApprovalHarness(request)

    async with app.run_test() as pilot:
        await pilot.pause()
        rendered = str(app.screen.query_one(".select-hint", Static).render())
        assert "工具：run_command" in rendered
        assert f"路径：{target}" in rendered
        assert "权限：execute" in rendered
        assert "命令：Get-Content approved.txt" in rendered
        for _ in range(down):
            await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

    assert app.selected is expected
