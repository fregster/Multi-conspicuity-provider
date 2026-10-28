"""
Per-client compression in front of the stream's SBS and JSON ports. Each client
connection gets its own upstream connection to the stream and ONE fresh
compressed stream (STREAM_COMPRESSION):

  gzip  a single gzip member (RFC 1952), Z_SYNC_FLUSH at least every FLUSH_MS
  zstd  a single zstd frame, FLUSH_BLOCK at least every FLUSH_MS
  none  upstream bytes passed through unchanged

Data is never held longer than FLUSH_MS, so a client sees a decodable prefix
within that time of the stream going quiet. A config change means the container
is recreated and every connection drops; that is how clients pick up a new
encoding. The ports are output-only, so client bytes are discarded, never sent
upstream. Upstream EOF or error closes the client (after finishing the
compressed stream); a client that is gone, or that falls MAX_BUFFER bytes
behind, closes the upstream. Stdlib only.
"""

import asyncio
import contextlib
import functools
import os
import time
import zlib
from collections.abc import Mapping
from compression import zstd
from dataclasses import dataclass
from typing import Protocol

Upstream = tuple[str, int]

MAX_BUFFER = 4 * 1024 * 1024  # bytes queued to one client before it is dropped
CONNECT_TIMEOUT_S = 10
LEVELS = {"gzip": (1, 9, 6), "zstd": (1, 19, 3)}  # min, max, default
FLUSH_MS_RANGE = (1, 10_000)
DEFAULT_ROUTES = "30003:stream:30003,30047:stream:30047"


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


class Codec(Protocol):
    def write(self, data: bytes) -> bytes: ...
    def flush(self) -> bytes: ...
    def close(self) -> bytes: ...


class Passthrough:
    def write(self, data: bytes) -> bytes:
        return data

    def flush(self) -> bytes:
        return b""

    def close(self) -> bytes:
        return b""


class Gzip:
    def __init__(self, level: int) -> None:
        self._z = zlib.compressobj(level, zlib.DEFLATED, 16 + zlib.MAX_WBITS)

    def write(self, data: bytes) -> bytes:
        return self._z.compress(data)

    def flush(self) -> bytes:
        return self._z.flush(zlib.Z_SYNC_FLUSH)

    def close(self) -> bytes:
        return self._z.flush(zlib.Z_FINISH)


class Zstd:
    def __init__(self, level: int) -> None:
        self._z = zstd.ZstdCompressor(level=level)

    def write(self, data: bytes) -> bytes:
        return self._z.compress(data)

    def flush(self) -> bytes:
        return self._z.flush(zstd.ZstdCompressor.FLUSH_BLOCK)

    def close(self) -> bytes:
        return self._z.flush(zstd.ZstdCompressor.FLUSH_FRAME)


@dataclass(frozen=True)
class Config:
    method: str
    level: int
    flush_s: float
    routes: list[tuple[int, Upstream]]

    def codec(self) -> Codec:
        if self.method == "gzip":
            return Gzip(self.level)
        if self.method == "zstd":
            return Zstd(self.level)
        return Passthrough()


def int_setting(name: str, raw: str, low: int, high: int) -> int:
    try:
        value = int(raw)
    except ValueError:
        value = low - 1
    if not low <= value <= high:
        msg = f"{name}={raw!r}: want an integer from {low} to {high}"
        raise ValueError(msg)
    return value


def parse(env: Mapping[str, str]) -> Config:
    """Settings from the environment; ValueError says what is wrong."""
    method = env.get("STREAM_COMPRESSION", "").strip().lower() or "none"
    if method != "none" and method not in LEVELS:
        msg = f"STREAM_COMPRESSION={method!r}: want none, gzip or zstd"
        raise ValueError(msg)
    level = 0
    if method != "none":
        low, high, default = LEVELS[method]
        raw = env.get("STREAM_COMPRESSION_LEVEL", "").strip()
        level = int_setting("STREAM_COMPRESSION_LEVEL", raw, low, high) if raw else default
    raw_ms = env.get("STREAM_COMPRESSION_FLUSH_MS", "").strip() or "100"
    flush_ms = int_setting("STREAM_COMPRESSION_FLUSH_MS", raw_ms, *FLUSH_MS_RANGE)
    routes = []
    for r in (env.get("COMPRESS_ROUTES") or DEFAULT_ROUTES).split(","):
        listen, host, port = r.split(":")
        routes.append((int(listen), (host, int(port))))
    return Config(method, level, flush_ms / 1000, routes)


async def to_client(up: asyncio.StreamReader, out: asyncio.StreamWriter, codec: Codec, flush_s: float) -> None:
    """Upstream to client until upstream EOF or the client falls too far behind."""
    transport = out.transport
    deadline: float | None = None  # when the oldest unflushed byte must go out
    while True:
        wait = None if deadline is None else max(0.0, deadline - time.monotonic())
        try:
            data = await asyncio.wait_for(up.read(65536), wait)
        except TimeoutError:
            data = None
        if data == b"":
            out.write(codec.close())
            return
        chunk = codec.write(data) if data else b""
        if data and deadline is None:
            deadline = time.monotonic() + flush_s
        if deadline is not None and time.monotonic() >= deadline:
            chunk += codec.flush()
            deadline = None
        out.write(chunk)
        if transport.get_write_buffer_size() > MAX_BUFFER:
            log("client stalled, dropped")
            return


async def client_gone(reader: asyncio.StreamReader) -> None:
    while await reader.read(4096):
        pass


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, upstream: Upstream, cfg: Config) -> None:
    up_writer = None
    try:
        up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(*upstream), CONNECT_TIMEOUT_S)
        tasks = [
            asyncio.ensure_future(to_client(up_reader, writer, cfg.codec(), cfg.flush_s)),
            asyncio.ensure_future(client_gone(reader)),
        ]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    except (OSError, TimeoutError) as e:
        log(f"{upstream[0]}:{upstream[1]}: {e}")
    finally:
        if writer.transport.get_write_buffer_size():
            writer.transport.abort()  # a stalled client never drains, so close() would wait forever
        for w in (up_writer, writer):
            if w is not None:
                w.close()
                with contextlib.suppress(OSError):
                    await w.wait_closed()


async def serve(cfg: Config) -> None:
    servers = []
    for port, upstream in cfg.routes:
        servers.append(
            await asyncio.start_server(
                functools.partial(handle, upstream=upstream, cfg=cfg),
                "0.0.0.0",  # noqa: S104  # container port, published on loopback only
                port,
            )
        )
        log(f"{cfg.method} level {cfg.level} flush {cfg.flush_s * 1000:.0f}ms: :{port} -> {upstream[0]}:{upstream[1]}")
    await asyncio.gather(*(s.serve_forever() for s in servers))


def main() -> None:
    try:
        cfg = parse(os.environ)
    except ValueError as e:
        msg = f"stream-compress: {e}"
        raise SystemExit(msg) from e
    asyncio.run(serve(cfg))


if __name__ == "__main__":
    main()
