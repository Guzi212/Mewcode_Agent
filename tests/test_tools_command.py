from pathlib import Path

from mewcode.tools.command import run_command
from mewcode.tools.models import ToolCall


def test_command_returns_stdout_stderr_and_exit_code(tmp_path: Path):
    result = run_command(
        ToolCall("1", "run_command", {"command": "echo ok; echo warn >&2", "cwd": "."}),
        tmp_path,
    )
    assert result.ok
    assert "stdout:\nok" in result.output
    assert "stderr:\nwarn" in result.output
    assert "exit_code: 0" in result.output


def test_nonzero_command_is_structured_error(tmp_path: Path):
    result = run_command(ToolCall("1", "run_command", {"command": "exit 7"}), tmp_path)
    assert not result.ok
    assert result.error.code == "command_failed"
    assert "exit_code: 7" in result.output
