import pytest

from mewcode.prompts import (
    ACTIVE_SKILLS_MODULE,
    CUSTOM_INSTRUCTIONS_MODULE,
    DEFAULT_PROMPT_MODULES,
    LONG_TERM_MEMORY_MODULE,
    PromptModule,
    SYSTEM_PROMPT,
    build_plan_mode_reminder,
    build_system_prompt,
    build_system_reminder,
)
from mewcode.messages import ConversationItem, ConversationItemKind
from mewcode.providers.anthropic import _serialize as serialize_anthropic
from mewcode.providers.openai import _serialize as serialize_openai
from mewcode.tools.registry import build_default_registry


def test_default_modules_have_strict_order_and_single_blank_lines():
    markers = [
        "你是 MewCode",
        "遵循系统级指令",
        "默认处于执行模式",
        "开始动作前",
        "优先使用最贴合任务的专用工具",
        "默认使用简体中文",
        "最终答复聚焦实际结果",
    ]

    assert [SYSTEM_PROMPT.index(marker) for marker in markers] == sorted(
        SYSTEM_PROMPT.index(marker) for marker in markers
    )
    assert "\n\n\n" not in SYSTEM_PROMPT
    assert SYSTEM_PROMPT == SYSTEM_PROMPT.strip()


def test_empty_optional_modules_are_skipped_without_special_cases():
    assert CUSTOM_INSTRUCTIONS_MODULE.content == ""
    assert ACTIVE_SKILLS_MODULE.content == ""
    assert LONG_TERM_MEMORY_MODULE.content == ""
    assert build_system_prompt(DEFAULT_PROMPT_MODULES) == SYSTEM_PROMPT


def test_priority_sort_is_stable_and_new_module_only_needs_declaration():
    modules = [
        PromptModule("low", 10, "low"),
        PromptModule("first", 20, "first"),
        PromptModule("second", 20, "second"),
        PromptModule("empty", 30, "  "),
    ]

    assert build_system_prompt(modules) == "first\n\nsecond\n\nlow"
    assert build_system_prompt(modules).encode() == build_system_prompt(
        modules
    ).encode()


def test_system_reminder_is_tagged_and_rejects_empty_content():
    assert build_system_reminder("当前约束") == (
        "<system-reminder>\n当前约束\n</system-reminder>"
    )
    with pytest.raises(ValueError, match="不能为空"):
        build_system_reminder("  ")


def test_plan_mode_full_reminder_repeats_every_five_rounds():
    reminders = [build_plan_mode_reminder(index) for index in range(1, 18)]
    full_rounds = [
        index
        for index, reminder in enumerate(reminders, start=1)
        if "具体、可执行且可验证" in reminder
    ]

    assert full_rounds == [1, 6, 11, 16]
    assert all("只允许调查和规划" in reminders[index - 1] for index in [2, 5, 17])
    with pytest.raises(ValueError, match="从 1 开始"):
        build_plan_mode_reminder(0)


def test_critical_tool_rules_exist_in_prompt_and_relevant_descriptions():
    descriptions = {
        definition.name: definition.description
        for definition in build_default_registry().definitions()
    }

    assert "不要用 run_command" in SYSTEM_PROMPT
    assert "编辑既有文件前必须先读取" in SYSTEM_PROMPT
    assert "局部修改优先使用精确 edit_file" in SYSTEM_PROMPT
    assert "只依据工具实际返回" in SYSTEM_PROMPT
    assert "优先使用本工具" in descriptions["find_files"]
    assert "优先使用本工具" in descriptions["search_code"]
    assert "编辑既有文件前必须先" in descriptions["read_file"]
    assert "精确替换" in descriptions["edit_file"]
    assert "整文件覆盖掩盖" in descriptions["edit_file"]
    assert "只依据实际命令结果" in descriptions["run_command"]


def test_protocols_receive_equivalent_system_and_user_content():
    history = [
        ConversationItem.text_item(ConversationItemKind.SYSTEM, "stable"),
        ConversationItem.system_reminder(
            "<system-reminder>dynamic</system-reminder>"
        ),
        ConversationItem.text_item(ConversationItemKind.USER, "task"),
    ]

    anthropic_system, anthropic_messages = serialize_anthropic(history)
    openai_messages = serialize_openai(history)

    assert [block["text"] for block in anthropic_system] == [
        message["content"] for message in openai_messages[:2]
    ]
    assert anthropic_messages[0]["content"] == openai_messages[2]["content"]
