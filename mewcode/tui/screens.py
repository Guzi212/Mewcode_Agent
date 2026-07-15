"""多 provider 时的方向键选择屏。"""

from __future__ import annotations

from textual.screen import Screen
from textual.widgets import Label, ListItem, ListView, Static

from ..config import ProviderConfig
from ..sandbox import AccessRequest, ApprovalScope


class ProviderSelectScreen(Screen[ProviderConfig]):
    """列出各 provider（name — model），方向键选择、回车确认。"""

    def __init__(self, providers: list[ProviderConfig]) -> None:
        super().__init__()
        self._providers = providers

    def compose(self):
        yield Static("选择要使用的 provider（↑↓ 移动，回车确认）：", classes="select-hint")
        yield ListView(
            *[
                ListItem(Label(f"{p.name} — {p.model}"))
                for p in self._providers
            ]
        )

    def on_mount(self) -> None:
        self.query_one(ListView).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index or 0
        self.dismiss(self._providers[index])


class ToolApprovalScreen(Screen[ApprovalScope]):
    """工作目录外路径访问的方向键审批界面。"""

    def __init__(self, request: AccessRequest) -> None:
        super().__init__()
        self._request = request
        self._scopes = [ApprovalScope.DENY, ApprovalScope.ONCE, ApprovalScope.SESSION]

    def compose(self):
        command = (
            f"\n命令：{self._request.command_summary}"
            if self._request.command_summary
            else ""
        )
        yield Static(
            "工具请求访问工作目录外路径：\n"
            f"工具：{self._request.tool_name}\n"
            f"路径：{self._request.target}\n"
            f"权限：{self._request.mode.value}{command}\n\n"
            "↑↓ 选择，Enter 确认：",
            classes="select-hint",
        )
        yield ListView(
            ListItem(Label("拒绝")),
            ListItem(Label("仅本次允许")),
            ListItem(Label("本会话允许")),
        )

    def on_mount(self) -> None:
        self.query_one(ListView).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.dismiss(self._scopes[event.list_view.index or 0])
