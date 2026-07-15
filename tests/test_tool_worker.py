import json
from pathlib import Path
import subprocess
import sys


def test_worker_json_lines_round_trip(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("hello\n")
    payload = {
        "call": {"id": "call-1", "name": "read_file", "arguments": {"path": "notes.txt"}},
        "workspace": str(tmp_path),
    }
    completed = subprocess.run(
        [sys.executable, "-m", "mewcode.tool_worker"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert result["output"] == "1: hello"


def test_worker_invalid_request_is_structured():
    completed = subprocess.run(
        [sys.executable, "-m", "mewcode.tool_worker"],
        input="not-json",
        text=True,
        capture_output=True,
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"
