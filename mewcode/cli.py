"""命令行入口：加载配置并启动 TUI。无行为性 flag（对齐 spec「不做 flag 覆盖」）。"""

from __future__ import annotations

import sys

from .config import load_config
from .errors import MewCodeError
from .tui.app import MewCodeApp


def main() -> int:
    try:
        config = load_config()
        MewCodeApp(config).run()
        return 0
    except MewCodeError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1
