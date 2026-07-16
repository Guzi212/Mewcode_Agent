from __future__ import annotations

import platform

import pytest

from mewcode.sandbox import SandboxState
from mewcode.sandbox.windows import WindowsSandbox


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-windows-native",
        action="store_true",
        default=False,
        help="运行会启动真实 AppContainer 并临时修改 NTFS ACL 的 Windows 测试",
    )


@pytest.fixture
def windows_native(request: pytest.FixtureRequest) -> WindowsSandbox:
    if not request.config.getoption("--run-windows-native"):
        pytest.skip("需要显式传入 --run-windows-native")
    if platform.system() != "Windows":
        pytest.skip("Windows 原生 AppContainer 测试仅在 Windows 运行")
    sandbox = WindowsSandbox()
    diagnostic = sandbox.diagnose()
    if diagnostic.state is not SandboxState.READY:
        pytest.fail(
            f"Windows 原生沙箱前置未就绪：{diagnostic.code} / {diagnostic.message}"
        )
    return sandbox
