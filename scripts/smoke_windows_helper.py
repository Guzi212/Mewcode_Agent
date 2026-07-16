"""对已构建 Windows Helper 执行真实 AppContainer 冒烟测试。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import uuid


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--helper", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument(
        "--scenario",
        choices=(
            "diagnose",
            "write",
            "read",
            "edit",
            "find",
            "search",
            "env",
            "acl",
            "nonzero",
            "outside",
            "outside_read",
            "network",
            "timeout",
        ),
        required=True,
    )
    parser.add_argument("--path", default="created/result.txt")
    parser.add_argument("--host-timeout", type=float, default=120.0)
    parser.add_argument("--pause-ms", type=int, default=0)
    parser.add_argument("--command")
    return parser.parse_args()


def scenario_call(
    name: str, path: str, command: str | None = None
) -> tuple[str, dict[str, object], int]:
    if command is not None:
        return "run_command", {"command": command}, 250 if name == "timeout" else 5_000
    if name == "write":
        return (
            "write_file",
            {"path": path, "content": "hello from appcontainer write"},
            5_000,
        )
    if name == "read":
        return "read_file", {"path": path}, 5_000
    if name == "edit":
        return (
            "edit_file",
            {
                "path": path,
                "old_text": "hello from appcontainer write",
                "new_text": "hello from appcontainer edit",
            },
            5_000,
        )
    if name == "find":
        return "find_files", {"pattern": "**/*.txt", "path": "."}, 5_000
    if name == "search":
        return (
            "search_code",
            {"pattern": "hello from appcontainer edit", "path": "."},
            5_000,
        )
    if name == "env":
        return "run_command", {"command": "Write-Output $env:MEWCODE_SANDBOX"}, 5_000
    if name == "acl":
        return "run_command", {"command": "whoami.exe /all; icacls.exe created"}, 5_000
    if name == "nonzero":
        return (
            "run_command",
            {"command": "[Console]::Error.WriteLine('expected failure'); exit 7"},
            5_000,
        )
    if name == "outside":
        return "write_file", {"path": path, "content": "must not be written"}, 5_000
    if name == "outside_read":
        return "read_file", {"path": path}, 5_000
    return "run_command", {"command": "Start-Sleep -Seconds 5"}, 250


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    helper = args.helper.resolve(strict=True)
    python = args.python.resolve(strict=True)
    workspace = args.workspace.resolve(strict=True)
    if not workspace.is_dir():
        raise SystemExit("workspace 必须是已存在的目录")

    request_id = f"smoke-{args.scenario}-{uuid.uuid4().hex}"
    listener = None
    network_connected = False
    if args.scenario == "network" and args.command is None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(0.25)
        port = listener.getsockname()[1]
        args.command = (
            "$client = [Net.Sockets.TcpClient]::new(); "
            "try { "
            f"$pending = $client.BeginConnect('127.0.0.1', {port}, $null, $null); "
            "if (-not $pending.AsyncWaitHandle.WaitOne(1000)) { "
            "[Console]::Error.WriteLine('network blocked'); exit 9 }; "
            "$client.EndConnect($pending); "
            "Write-Output 'unexpected connection'; exit 0 "
            "} catch { [Console]::Error.WriteLine('network blocked'); exit 9 } "
            "finally { $client.Dispose() }"
        )
    if args.scenario == "diagnose":
        request = {"protocol_version": 1, "operation": "diagnose"}
    else:
        tool_name, arguments, timeout_ms = scenario_call(
            args.scenario, args.path, args.command
        )
        request = {
            "protocol_version": 1,
            "operation": "run",
            "request_id": request_id,
            "timeout_ms": timeout_ms,
            "python_executable": str(python),
            "workspace": str(workspace),
            "grants": [],
            "worker_payload": {
                "call": {"id": request_id, "name": tool_name, "arguments": arguments},
                "workspace": str(workspace),
            },
        }
    environment = os.environ.copy()
    environment["MEWCODE_HELPER_TRACE"] = "1"
    if args.pause_ms:
        environment["MEWCODE_HELPER_PAUSE_MS"] = str(args.pause_ms)
    try:
        completed = subprocess.run(
            [str(helper)],
            input=json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n",
            text=True,
            encoding="utf-8",
            errors="strict",
            capture_output=True,
            timeout=args.host_timeout,
            check=False,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        for value, stream in ((exc.stdout, sys.stdout), (exc.stderr, sys.stderr)):
            if value:
                text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
                print(text, file=stream, end="")
        print(f"helper host timeout after {args.host_timeout:.1f}s", file=sys.stderr)
        return 1
    if listener is not None:
        try:
            connection, _ = listener.accept()
        except TimeoutError:
            pass
        else:
            network_connected = True
            connection.close()
        finally:
            listener.close()
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError:
        print(completed.stdout, end="")
        return 1
    print(json.dumps(response, ensure_ascii=False, indent=2))

    if completed.returncode != 0:
        return 1
    if args.scenario == "diagnose":
        return 0 if response.get("state") == "ready" else 1
    if response.get("request_id") != request_id:
        return 1
    if args.scenario == "timeout":
        error = response.get("error") or {}
        return 0 if response.get("status") == "timed_out" and error.get("code") == "sandbox_timeout" else 1
    worker = response.get("worker_result") or {}
    if args.scenario in {"nonzero", "outside", "outside_read", "network"}:
        error = worker.get("error") or {}
        if args.scenario == "outside":
            expected_code = "file_write_error"
        elif args.scenario == "outside_read":
            expected_code = "file_read_error"
        else:
            expected_code = "command_failed"
        if response.get("status") != "completed" or worker.get("ok") or error.get("code") != expected_code:
            return 1
        if args.scenario == "network" and network_connected:
            return 1
        if args.scenario == "nonzero" and "exit_code: 7" not in worker.get("output", ""):
            return 1
        return 0
    if response.get("status") != "completed" or not worker.get("ok"):
        return 1
    output = worker.get("output", "")
    if args.scenario == "read" and "hello from appcontainer write" not in output:
        return 1
    if args.scenario == "env" and "windows-appcontainer" not in output:
        return 1
    if args.scenario in {"find", "search"} and "result.txt" not in output:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
