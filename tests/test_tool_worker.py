import json
from pathlib import Path
import subprocess
import sys


def _run_worker(payload: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mewcode.tool_worker"],
        input=payload,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )


def test_worker_json_lines_round_trip(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("hello\n")
    payload = {
        "call": {
            "id": "call-1",
            "name": "read_file",
            "arguments": {"path": "notes.txt"},
        },
        "workspace": str(tmp_path),
    }
    completed = _run_worker(json.dumps(payload) + "\n")
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert result["output"] == "1: hello"


def test_worker_invalid_request_is_structured():
    result = json.loads(_run_worker("not-json\n").stdout)
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"


def test_worker_rejects_multiple_json_lines():
    completed = _run_worker("{}\n{}\n")
    result = json.loads(completed.stdout)
    assert result["error"]["code"] == "invalid_request"
    assert completed.stdout.count("\n") == 1


def test_worker_rejects_unknown_fields(tmp_path: Path):
    payload = {
        "call": {"id": "call-1", "name": "read_file", "arguments": {}},
        "workspace": str(tmp_path),
        "extra": True,
    }
    result = json.loads(_run_worker(json.dumps(payload) + "\n").stdout)
    assert result["error"]["code"] == "invalid_request"


def test_worker_rejects_oversized_request():
    completed = _run_worker(("x" * (4 * 1024 * 1024 + 1)) + "\n")
    result = json.loads(completed.stdout)
    assert result["error"]["code"] == "invalid_request"
