"""Run: python3 test_compress.py"""

import asyncio
import contextlib
import functools
import time
import zlib
from collections.abc import Awaitable, Callable
from compression import zstd
from typing import Any, Protocol, Self
from unittest import mock

import compress

GZIP_MAGIC = b"\x1f\x8b"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
LINES = [f"MSG,3,,,{i:06X},,,,,,,{i},,,,,,,,,,\r\n".encode() for i in range(200)]
FLUSH_S = 0.1
MARGIN_S = 0.4


def cfg(
    method: str = "none", level: int = 0, routes: list[tuple[int, compress.Upstream]] | None = None
) -> compress.Config:
    return compress.Config(method, level, FLUSH_S, routes or [])


# --- parse() ---------------------------------------------------------------


def parsed(**env: str) -> compress.Config:
    return compress.parse({k.upper(): v for k, v in env.items()})


def rejected(**env: str) -> str:
    try:
        parsed(**env)
    except ValueError as e:
        return str(e)
    raise AssertionError(env)


default = parsed()
assert (default.method, default.flush_s) == ("none", 0.1)
assert default.routes == [(30003, ("stream", 30003)), (30047, ("stream", 30047))]
assert parsed(stream_compression="none").method == "none"
assert parsed(stream_compression=" GZip ").method == "gzip"
assert (parsed(stream_compression="gzip").level, parsed(stream_compression="zstd").level) == (6, 3)
assert parsed(stream_compression="gzip", stream_compression_level="").level == 6  # blank = default
assert parsed(stream_compression="none", stream_compression_level="99").level == 0  # unused with none
for method, low, high in (("gzip", 1, 9), ("zstd", 1, 19)):
    assert parsed(stream_compression=method, stream_compression_level=str(low)).level == low
    assert parsed(stream_compression=method, stream_compression_level=str(high)).level == high
    for bad in (str(low - 1), str(high + 1), "fast", "3.5"):
        assert "STREAM_COMPRESSION_LEVEL" in rejected(stream_compression=method, stream_compression_level=bad)
assert "want none, gzip or zstd" in rejected(stream_compression="brotli")
assert parsed(stream_compression_flush_ms="250").flush_s == 0.25
assert parsed(stream_compression_flush_ms="1").flush_s == 0.001
assert parsed(stream_compression_flush_ms="10000").flush_s == 10
for bad in ("0", "10001", "-5", "soon"):
    assert "STREAM_COMPRESSION_FLUSH_MS" in rejected(stream_compression_flush_ms=bad)
assert parsed(compress_routes="1:a:2,3:b:4").routes == [(1, ("a", 2)), (3, ("b", 4))]

# --- codecs ----------------------------------------------------------------

assert isinstance(cfg("gzip", 6).codec(), compress.Gzip)
assert isinstance(cfg("zstd", 3).codec(), compress.Zstd)
assert isinstance(cfg().codec(), compress.Passthrough)
assert compress.Passthrough().write(b"abc") == b"abc"
assert compress.Passthrough().close() == b""

# --- relay, against a real local upstream and client -----------------------


Reader = asyncio.StreamReader
Writer = asyncio.StreamWriter
Script = Callable[[Reader, Writer], Awaitable[None]]


class Decoder(Protocol):
    def decompress(self, data: bytes) -> bytes: ...


class Upstream:
    """Fake stream: runs `script(reader, writer)` for each connection."""

    def __init__(self, script: Script) -> None:
        self.script = script
        self.server: asyncio.Server | None = None
        self.closed = asyncio.Event()  # set when the relay closes our side
        self.addr: compress.Upstream = ("", 0)

    async def __aenter__(self) -> Self:
        self.server = await asyncio.start_server(self._run, "127.0.0.1", 0)
        self.addr = self.server.sockets[0].getsockname()[:2]
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self.server
        self.server.close()

    async def _run(self, reader: Reader, writer: Writer) -> None:
        try:
            with contextlib.suppress(ConnectionError):  # the relay hung up on us: expected
                await self.script(reader, writer)
        finally:
            self.closed.set()
            writer.close()


async def relay(config: compress.Config, upstream: compress.Upstream) -> tuple[asyncio.Server, compress.Upstream]:
    """The relay listening on a free port; returns (server, (host, port))."""
    srv = await asyncio.start_server(functools.partial(compress.handle, upstream=upstream, cfg=config), "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[:2]


async def send_then_eof(reader: Reader, writer: Writer) -> None:
    for i in range(0, len(LINES), 20):
        writer.write(b"".join(LINES[i : i + 20]))
        await writer.drain()
        await asyncio.sleep(0.01)


async def read_all(config: compress.Config, script: Script = send_then_eof, client_sends: bytes = b"") -> bytes:
    async with Upstream(script) as up:
        srv, addr = await relay(config, up.addr)
        r, w = await asyncio.open_connection(*addr)
        w.write(client_sends)  # ignored: the ports are output-only
        data = await asyncio.wait_for(r.read(), 10)
        w.close()
        srv.close()
        return data


async def test_roundtrips() -> None:
    sent = b"".join(LINES)
    assert await read_all(cfg()) == sent  # none: byte-identical
    assert await read_all(cfg(), client_sends=b"hello") == sent

    for level in (1, 6, 9):
        data = await read_all(cfg("gzip", level))
        assert data[:2] == GZIP_MAGIC
        z = zlib.decompressobj(16 + zlib.MAX_WBITS)
        assert z.decompress(data) == sent
        assert z.eof  # one complete gzip member, trailer included
        assert z.unused_data == b""
        assert len(data) < len(sent) / 2

    for level in (1, 3, 19):
        data = await read_all(cfg("zstd", level), client_sends=b"x" * 10000)
        assert data[:4] == ZSTD_MAGIC
        zd = zstd.ZstdDecompressor()
        assert zd.decompress(data) == sent
        assert zd.eof  # one complete frame
        assert zd.unused_data == b""
        assert len(data) < len(sent) / 2


async def prefix_arrival(config: compress.Config, decoder: Callable[[], Decoder], script: Script) -> float:
    """Seconds until the client holds a decodable prefix containing the first line."""
    async with Upstream(script) as up:
        srv, addr = await relay(config, up.addr)
        r, w = await asyncio.open_connection(*addr)
        start = time.monotonic()
        out = b""
        decode = decoder()
        while LINES[0] not in out:
            chunk = await asyncio.wait_for(r.read(65536), 5)
            assert chunk, "relay closed before the prefix arrived"
            out += decode.decompress(chunk)
        elapsed = time.monotonic() - start
        w.close()
        srv.close()
        return elapsed


class Identity:
    def decompress(self, data: bytes) -> bytes:
        return data


async def write_then_idle(reader: Reader, writer: Writer) -> None:
    writer.write(LINES[0])
    await writer.drain()
    await reader.read()  # hold the connection open until the relay hangs up


async def write_steadily(reader: Reader, writer: Writer) -> None:
    # Data never goes idle for FLUSH_S, so only the deadline can flush it.
    for _ in range(100):
        writer.write(LINES[0])
        await writer.drain()
        await asyncio.sleep(0.01)
    await reader.read()


async def test_flush_timing() -> None:
    gz = functools.partial(zlib.decompressobj, 16 + zlib.MAX_WBITS)
    for script in (write_then_idle, write_steadily):
        assert await prefix_arrival(cfg("gzip", 6), gz, script) < FLUSH_S + MARGIN_S
        assert await prefix_arrival(cfg("zstd", 3), zstd.ZstdDecompressor, script) < FLUSH_S + MARGIN_S
    assert await prefix_arrival(cfg(), Identity, write_then_idle) < FLUSH_S  # no buffering at all


async def test_closing() -> None:
    # Upstream resets the connection: the client is closed, not left hanging.
    async def reset(reader: Reader, writer: Writer) -> None:
        writer.write(LINES[0])
        await writer.drain()
        await asyncio.sleep(0.05)
        writer.transport.abort()

    assert await read_all(cfg(), reset) == LINES[0]

    # Upstream down: the client is closed and the relay keeps serving.
    dead = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    addr = dead.sockets[0].getsockname()[:2]
    dead.close()
    await dead.wait_closed()
    srv, relay_addr = await relay(cfg(), addr)
    r, w = await asyncio.open_connection(*relay_addr)
    assert await asyncio.wait_for(r.read(), 5) == b""
    w.close()
    srv.close()

    # Client goes away: the upstream connection is closed too.
    async with Upstream(write_then_idle) as up:
        srv, relay_addr = await relay(cfg("gzip", 6), up.addr)
        r, w = await asyncio.open_connection(*relay_addr)
        assert await asyncio.wait_for(r.read(1), 5)
        w.close()
        await asyncio.wait_for(up.closed.wait(), 5)
        srv.close()


async def test_slow_client() -> None:
    sent = 0

    async def flood(reader: Reader, writer: Writer) -> None:
        nonlocal sent
        chunk = bytes(65536)
        while sent < 512 * 1024 * 1024:  # the relay must hang up long before this
            writer.write(chunk)
            await writer.drain()
            sent += len(chunk)

    with mock.patch.object(compress, "MAX_BUFFER", 256 * 1024):
        async with Upstream(flood) as up:
            srv, addr = await relay(cfg(), up.addr)
            _r, w = await asyncio.open_connection(*addr)  # never reads
            await asyncio.wait_for(up.closed.wait(), 30)
            w.close()
            srv.close()
    assert sent < 512 * 1024 * 1024


async def main() -> None:
    await test_roundtrips()
    await test_flush_timing()
    await test_closing()
    await test_slow_client()


asyncio.run(main())

# --- serve() and main() ----------------------------------------------------

started: list[tuple[compress.Upstream, str, int]] = []


async def fake_start_server(handler: Any, host: str, port: int) -> mock.Mock:
    started.append((handler.keywords["upstream"], host, port))
    return mock.Mock(serve_forever=mock.AsyncMock())


with mock.patch.object(asyncio, "start_server", fake_start_server):
    asyncio.run(compress.serve(cfg("gzip", 6, [(30003, ("stream", 30003)), (30047, ("stream", 30047))])))
assert started == [(("stream", 30003), "0.0.0.0", 30003), (("stream", 30047), "0.0.0.0", 30047)]  # noqa: S104

with mock.patch.object(compress, "serve", new=mock.Mock()) as serve, mock.patch.object(asyncio, "run") as run:
    with mock.patch.dict("os.environ", {"STREAM_COMPRESSION": "zstd"}, clear=True):
        compress.main()
    assert serve.call_args.args[0].method == "zstd"
    assert run.called
    with mock.patch.dict("os.environ", {"STREAM_COMPRESSION": "zstd", "STREAM_COMPRESSION_LEVEL": "20"}, clear=True):
        try:
            compress.main()
        except SystemExit as e:
            assert "stream-compress: STREAM_COMPRESSION_LEVEL='20'" in str(e)
        else:
            raise AssertionError
print("ok")
