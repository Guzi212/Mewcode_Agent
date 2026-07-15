"""TUI 组件：banner、消息、思考计时、输入框、状态栏。"""

from __future__ import annotations

from textual import events
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Label, Markdown, Static, TextArea

CAT = r"""   /\_/\
  ( o.o )   MewCode
   > ^ <"""


class Banner(Static):
    """启动横幅：ASCII 猫咪 + 应用名/版本 + 当前工作目录。"""

    def __init__(self, version: str, cwd: str) -> None:
        super().__init__(
            f"{CAT}  v{version}\n"
            f"[dim]cwd: {cwd}[/dim]",
            classes="banner",
        )


class ReadyLine(Static):
    """一行就绪提示。"""

    def __init__(self) -> None:
        super().__init__(
            "[dim]已就绪。输入消息开始对话，/exit 或 Ctrl+C 退出。[/dim]",
            classes="ready",
        )


class UserMessage(Static):
    """用户消息。"""

    def __init__(self, text: str) -> None:
        super().__init__(f"[b cyan]❯[/b cyan] {text}", classes="msg user")


class ErrorMessage(Static):
    """错误消息（可区分样式）。"""

    def __init__(self, text: str) -> None:
        super().__init__(f"[b red]⚠ 出错：[/b red] {text}", classes="msg error")


class ToolMessage(Static):
    """工具调用行：先显示执行中，完成后显示摘要。"""

    def __init__(self, name: str, arguments: dict[str, object]) -> None:
        # Textual 的 Widget 内部也使用 _name；挂载后会覆盖它，导致工具行
        # 显示为 None。因此这里必须使用不与框架冲突的字段名。
        self._tool_name = name if name and name.casefold() not in {"none", "null"} else "未知工具"
        self._arguments = arguments
        super().__init__(self._render_text("执行中…"), classes="msg tool")

    def _render_text(self, suffix: str) -> str:
        key = "path" if "path" in self._arguments else "command" if "command" in self._arguments else "cwd"
        value = self._arguments.get(key, "")
        return f"[b yellow]● {self._tool_name}[/b yellow]({value}) [dim]{suffix}[/dim]"

    def complete(self, summary: str, ok: bool) -> None:
        mark = "✓" if ok else "⚠"
        self.update(self._render_text(f"{mark} {summary}"))


class AssistantMessage(Vertical):
    """助手消息：流式期纯文本逐字，结束后整段 markdown 定型。"""

    DEFAULT_CSS = """
    AssistantMessage {
        height: auto;
    }
    """

    def __init__(self) -> None:
        super().__init__(classes="msg assistant")
        self._text = ""
        self._stream = Static("", classes="assistant-stream")
        self._md = Markdown("")
        self._md.display = False

    def compose(self):
        yield self._stream
        yield self._md

    def append(self, delta: str) -> None:
        """流式追加纯文本增量。"""
        self._text += delta
        self._stream.update(self._text)

    @property
    def text(self) -> str:
        return self._text

    def finalize(self) -> None:
        """回复结束：整段以 markdown 重渲染定型。"""
        self._md.update(self._text)
        self._stream.display = False
        self._md.display = True


class ThinkingIndicator(Static):
    """等待/计时指示：进行中显示 Imagining… (Ns)，结束显示总耗时。"""

    def __init__(self) -> None:
        self._iteration = 0
        super().__init__("[dim]Imagining… (0s)[/dim]", classes="thinking")

    def show_elapsed(self, seconds: int) -> None:
        iteration = f" · 第 {self._iteration} 轮" if self._iteration else ""
        self.update(f"[dim]Imagining…{iteration} ({seconds}s)[/dim]")

    def show_iteration(self, iteration: int, seconds: int = 0) -> None:
        self._iteration = iteration
        self.show_elapsed(seconds)

    def show_cancelling(self) -> None:
        self.update("[dim]正在取消当前任务…[/dim]")

    def show_total(self, seconds: int) -> None:
        self.update(f"[dim]⏱ {seconds}s[/dim]")


class StatusBar(Horizontal):
    """底部状态栏：左侧 provider 名，右侧 model。"""

    def __init__(self) -> None:
        super().__init__(classes="statusbar")
        self._left = Label("", classes="status-left")
        self._right = Label("", classes="status-right")
        self._provider_name = ""
        self._model = ""
        self._input_tokens: int | None = None
        self._output_tokens: int | None = None

    def compose(self):
        yield self._left
        yield self._right

    def set_provider(self, name: str, model: str) -> None:
        self._provider_name = name
        self._model = model
        self._render_status()

    def set_usage(
        self, input_tokens: int | None, output_tokens: int | None
    ) -> None:
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._render_status()

    def _render_status(self) -> None:
        self._left.update(self._provider_name)
        input_text = "?" if self._input_tokens is None else str(self._input_tokens)
        output_text = "?" if self._output_tokens is None else str(self._output_tokens)
        self._right.update(
            f"{self._model} · tokens in {input_text} / out {output_text}"
        )


class PromptInput(TextArea):
    """多行输入框：Alt+Enter 换行、Enter 提交、提交后清空。"""

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def on_mount(self) -> None:
        self.border_title = "❯ Send a message..."

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            text = self.text.strip()
            if text:
                self.post_message(self.Submitted(text))
            return
        if event.key == "alt+enter":
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        await super()._on_key(event)
