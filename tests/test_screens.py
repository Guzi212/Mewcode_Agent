from textual.app import App

from mewcode.config import ProviderConfig
from mewcode.tui.screens import ProviderSelectScreen


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
