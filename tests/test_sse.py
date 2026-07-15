from mewcode.providers.sse import iter_sse


class FakeResponse:
    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


async def test_iter_sse_extracts_data_payloads():
    resp = FakeResponse(
        [
            'data: {"a":1}',
            "",
            "event: ping",
            "data: [DONE]",
            "",
        ]
    )
    got = [x async for x in iter_sse(resp)]
    assert got == ['{"a":1}', "[DONE]"]
