from pathlib import Path

from mewcode.tools.models import ToolCall
from mewcode.tools.search import find_files, search_code


def test_find_and_search_code(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("needle = 1\n")
    (tmp_path / "pkg" / "b.txt").write_text("needle too\n")

    found = find_files(ToolCall("1", "find_files", {"pattern": "**/*.py"}), tmp_path)
    searched = search_code(ToolCall("2", "search_code", {"pattern": "needle"}), tmp_path)

    assert found.ok and "a.py" in found.output
    assert searched.ok and "a.py:1: needle = 1" in searched.output


def test_search_no_result_is_successful_empty_result(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello")
    result = search_code(ToolCall("1", "search_code", {"pattern": "missing"}), tmp_path)
    assert result.ok
    assert result.output == ""
    assert result.summary == "未找到匹配内容"
