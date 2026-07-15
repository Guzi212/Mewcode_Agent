"""共享的 SSE 逐行解析工具。"""

from __future__ import annotations

from collections.abc import AsyncIterator


async def iter_sse(response) -> AsyncIterator[str]:
    """从 httpx 流式响应逐行提取 SSE data 负载。

    跳过空行与非 data 行；对 `data: <payload>` 产出 `<payload>`。
    `[DONE]` 哨兵与 JSON 解析交由调用方处理。
    """
    async for line in response.aiter_lines():
        if not line or not line.startswith("data:"):
            continue
        yield line[len("data:"):].lstrip()
