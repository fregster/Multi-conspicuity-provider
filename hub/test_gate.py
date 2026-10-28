"""Run: python3 test_gate.py"""

import json
import os
import socket
import threading
from unittest import mock

OK = "11111111-2222-3333-4444-555555555555"
os.environ["HUB_ALLOWED_UUIDS"] = f" {OK.upper()} ,other"
import gate

BEAST = b"\x1a\xe4" + OK.encode() + b"\x1aWO"
MLAT = json.dumps({"version": 3, "uuid": OK}).encode() + b"        \n"
assert gate.beast_uuid(BEAST) == OK
assert gate.beast_uuid(b"\x1a1" + bytes(40)) is None  # plain Beast, no UUID
assert gate.mlat_uuid(MLAT) == OK
assert gate.mlat_uuid(b'{"version": 3}\n') is None
assert gate.mlat_uuid(b"garbage\n") is None
assert gate.routes({"HUB_MLAT_PORT": "1"})[2] == (1, "mlat", ("127.0.0.1", 39090))
assert gate.routes({"GATE_ROUTES": "30003:open:39103,30047:open:39147"}) == [
    (30003, "open", ("127.0.0.1", 39103)),
    (30047, "open", ("127.0.0.1", 39147)),
]

# End to end: a fake upstream records what reaches it through the gate.
up = socket.create_server(("127.0.0.1", 0))
got: list[bytes] = []


def upstream() -> None:
    while True:
        c, _ = up.accept()
        got.append(c.recv(4096))
        c.sendall(b"reply")
        c.close()


threading.Thread(target=upstream, daemon=True).start()


def through(kind: str, hello: bytes, *, eof: bool = False, upstream: gate.Upstream | None = None) -> bytes:
    srv = socket.create_server(("127.0.0.1", 0))
    threading.Thread(target=gate.serve, args=(srv, kind, upstream or up.getsockname()), daemon=True).start()
    c = socket.create_connection(srv.getsockname())
    c.sendall(hello)
    if eof:
        c.shutdown(socket.SHUT_WR)
    c.settimeout(5)
    try:
        return c.recv(100)
    except ConnectionResetError:  # closed with our hello unread: dropped, same as EOF
        return b""


assert through("beast", BEAST + b"data") == b"reply"
assert got[-1].startswith(BEAST)
assert through("mlat", MLAT) == b"reply"
assert got[-1] == MLAT
n = len(got)
assert through("beast", BEAST.replace(b"1111", b"9999", 1)) == b""  # unknown UUID: dropped
assert through("mlat", b'{"uuid": "nope"}\n') == b""
assert len(got) == n  # nothing reached upstream
assert through("open", b"x") == b"reply"
assert got[-1] == b"x"
assert through("beast", BEAST[:10], eof=True) == b""  # hung up mid-hello: rejected
assert len(got) == n + 1

closed = socket.create_server(("127.0.0.1", 0))
dead = closed.getsockname()
closed.close()
assert through("open", b"x", upstream=dead) == b""  # upstream down: client dropped, gate keeps serving

# pipe(): a connection that errors mid-stream closes both ends.
src, dst = mock.Mock(spec=socket.socket), mock.Mock(spec=socket.socket)
src.recv.side_effect = ConnectionResetError
dst.shutdown.side_effect = OSError  # already gone: still closed
gate.pipe(src, dst)
assert src.close.called
assert dst.close.called

# main(): one listener per route, each handed to serve().
with (
    mock.patch.dict(os.environ, {"GATE_ROUTES": "0:open:39103,0:beast:39004"}),
    mock.patch.object(gate, "serve") as serve,
):
    gate.main()
assert [c.args[1:] for c in serve.call_args_list] == [
    ("open", ("127.0.0.1", 39103)),
    ("beast", ("127.0.0.1", 39004)),
]
print("ok")
