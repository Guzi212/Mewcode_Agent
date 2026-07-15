from pathlib import Path

from mewcode.tools.files import edit_file, read_file, write_file
from mewcode.tools.models import ToolCall


def test_read_file_adds_line_numbers(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("first\nsecond\n", encoding="utf-8")
    result = read_file(ToolCall("1", "read_file", {"path": "notes.txt"}), tmp_path)

    assert result.ok
    assert result.output == "1: first\n2: second"


def test_write_creates_parent_and_edit_requires_unique_match(tmp_path: Path):
    write = write_file(
        ToolCall("1", "write_file", {"path": "nested/config.txt", "content": "port=3000"}),
        tmp_path,
    )
    assert write.ok
    edited = edit_file(
        ToolCall(
            "2",
            "edit_file",
            {"path": "nested/config.txt", "old_text": "port=3000", "new_text": "port=4000"},
        ),
        tmp_path,
    )
    assert edited.ok
    assert (tmp_path / "nested/config.txt").read_text() == "port=4000"

    unchanged = edit_file(
        ToolCall(
            "3",
            "edit_file",
            {"path": "nested/config.txt", "old_text": "missing", "new_text": "x"},
        ),
        tmp_path,
    )
    assert not unchanged.ok
    assert unchanged.error.code == "edit_match_count"
    assert (tmp_path / "nested/config.txt").read_text() == "port=4000"
