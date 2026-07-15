from mewcode.conversation import Conversation
from mewcode.messages import ConversationItemKind, Role
from mewcode.tools.models import ToolCall, ToolResult


def test_build_context_order():
    c = Conversation("SYS")
    c.add_user("a")
    c.add_assistant("b")
    ctx = c.build_context()
    assert len(ctx) == 3
    assert ctx[0].role == Role.SYSTEM and ctx[0].content == "SYS"
    assert ctx[1].role == Role.USER and ctx[1].content == "a"
    assert ctx[2].role == Role.ASSISTANT and ctx[2].content == "b"


def test_empty_conversation_has_only_system():
    c = Conversation("SYS")
    ctx = c.build_context()
    assert len(ctx) == 1
    assert ctx[0].role == Role.SYSTEM


def test_history_keeps_tool_calls_and_results_in_order():
    conversation = Conversation("SYS")
    call = ToolCall("call-1", "read_file", {"path": "notes.txt"})
    result = ToolResult("call-1", "read_file", True, "1: hello", "读取 1 行")

    conversation.add_user("读文件")
    conversation.add_tool_calls([call])
    conversation.add_tool_results([result])
    conversation.add_assistant("文件内容是 hello")

    history = conversation.build_history()
    assert [item.kind for item in history] == [
        ConversationItemKind.SYSTEM,
        ConversationItemKind.USER,
        ConversationItemKind.TOOL_CALLS,
        ConversationItemKind.TOOL_RESULTS,
        ConversationItemKind.ASSISTANT,
    ]
    assert history[2].calls == (call,)
    assert history[3].results == (result,)


def test_assistant_turn_keeps_preamble_and_calls_together():
    conversation = Conversation("SYS")
    calls = [
        ToolCall("1", "read_file", {"path": "a"}),
        ToolCall("2", "find_files", {"pattern": "*"}),
    ]
    conversation.add_user("检查")
    conversation.add_assistant_turn("我先调查。", calls)
    conversation.add_tool_results(
        [
            ToolResult("1", "read_file", True, "a", "完成"),
            ToolResult("2", "find_files", True, "b", "完成"),
        ]
    )

    history = conversation.build_history("PLAN")
    assert history[0].text == "PLAN"
    assert history[2].kind == ConversationItemKind.ASSISTANT
    assert history[2].text == "我先调查。"
    assert history[2].calls == tuple(calls)
    assert [result.call_id for result in history[3].results] == ["1", "2"]
