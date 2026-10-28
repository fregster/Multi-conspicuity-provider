"""Run: python3 test_gate.py"""

import json
import os
import socket
import threading

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

# End to end: a fake upstream records what reaches it through the gate.
up = socket.create_server(("127.0.0.1", 0))
got = []


def upstream():
    while True:
        c, _ = up.accept()
        got.append(c.recv(4096))
        c.sendall(b"reply")
        c.close()


threading.Thread(target=upstream, daemon=True).start()


def through(kind, hello):
    srv = socket.create_server(("127.0.0.1", 0))
    threading.Thread(target=gate.serve, args=(srv, kind, up.getsockname()), daemon=True).start()
    c = socket.create_connection(srv.getsockname())
    c.sendall(hello)
    c.settimeout(5)
    return c.recv(100)


assert through("beast", BEAST + b"data") == b"reply" and got[-1].startswith(BEAST)
assert through("mlat", MLAT) == b"reply" and got[-1] == MLAT
n = len(got)
assert through("beast", BEAST.replace(b"1111", b"9999", 1)) == b""  # unknown UUID: dropped
assert through("mlat", b'{"uuid": "nope"}\n') == b""
assert len(got) == n  # nothing reached upstream
assert through("open", b"x") == b"reply" and got[-1] == b"x"  # Beast output: not checked
print("ok")
