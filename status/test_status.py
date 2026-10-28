"""Run: python3 test_status.py"""

import io
import json
import os
import socket
import time
import urllib.request
from http.server import ThreadingHTTPServer
from threading import Thread
from typing import Any
from unittest import mock

os.environ["ULTRAFEEDER_CONFIG"] = "adsb,in.adsb.lol,30004,beast_reduce_plus_out,uuid=x;mlat,in.adsb.lol,31090,uuid=x"
import status

assert status.MLAT == [("in.adsb.lol", "31090")]
prom = 'readsb_net_connector_status{host="in.adsb.lol",port="30004"} 3204\nreadsb_uptime 5\n'
assert status.beast(prom) == [{"host": "in.adsb.lol", "up_s": 3204}]

lol = {"clients": {"beast": [{"uuid": "x"}], "mlat": []}}
assert status.seen(lol, ("clients", "beast"), ("clients", "mlat")) == {"beast": True, "mlat": False}
assert status.seen({}, ("beast",), ("mlat",)) == {"beast": None, "mlat": None}  # API changed shape

# get(): sends a real User-Agent (aggregator APIs 403 Python's default).
seen_req: list[urllib.request.Request] = []


def fake_urlopen(req: urllib.request.Request, timeout: float) -> io.BytesIO:
    seen_req.append(req)
    return io.BytesIO(b"body")


with mock.patch.object(status, "urlopen", fake_urlopen):
    assert status.get("http://x/y") == b"body"
assert seen_req[0].full_url == "http://x/y"
assert seen_req[0].get_header("User-agent") == "multi-conspicuity-provider/status"

# Everything else fetches through get(): serve canned bodies by URL, OSError for the rest.
HOST = socket.gethostname()
ROWS = [
    {
        "Id": HOST + "abc",
        "Names": ["/status"],
        "State": "running",
        "Status": "Up",
        "Labels": {"com.docker.compose.project": "mcp"},
    },
    {
        "Id": "f00",
        "Names": ["/piaware"],
        "State": "running",
        "Status": "Up (healthy)",
        "Labels": {"com.docker.compose.project": "mcp"},
    },
    {"Id": "b4r", "Names": ["/elsewhere"], "State": "exited", "Status": "Exited", "Labels": {}},
]
BODIES: dict[str, Any] = {
    "http://socket-proxy:2375/containers/json?all=1": ROWS,
    "http://ultrafeeder/metrics": prom,
    "http://ultrafeeder/mlat-client-stats/in.adsb.lol:31090.json": {"peer_count": 4, "now": time.time() - 2},
    "https://api.adsb.fi/v1/myip": {"beast": [{}], "mlat": []},
    "http://ogn-bridge:8080/": {"connected": {"sbs": True}},
}


def fake_get(url: str) -> bytes:
    if url not in BODIES:
        raise OSError(url)
    body = BODIES[url]
    return body.encode() if isinstance(body, str) else json.dumps(body).encode()


with mock.patch.object(status, "get", fake_get):
    assert status.containers() == [
        {"name": "status", "state": "running", "status": "Up"},
        {"name": "piaware", "state": "running", "status": "Up (healthy)"},
    ]
    with mock.patch.object(status, "MLAT", [("in.adsb.lol", "31090"), ("nosync.example", "1")]):
        assert status.mlat() == [
            {"host": "in.adsb.lol", "peers": 4, "age_s": 2},
            {"host": "nosync.example", "peers": None, "age_s": None},  # no stats file yet
        ]
    remote = [
        {"site": "adsb.fi", "beast": True, "mlat": False},
        {"site": "airplanes.live", "beast": None, "mlat": None},  # API unreachable
    ]
    assert status.remote() == remote
    BODIES["https://api.adsb.fi/v1/myip"] = {}
    assert status.remote() == remote  # cached for REMOTE_TTL_S: third-party APIs
    status._remote["at"] -= status.REMOTE_TTL_S
    assert status.remote()[0] == {"site": "adsb.fi", "beast": None, "mlat": None}  # refetched

    page = status.status()
    assert page["beast"] == [{"host": "in.adsb.lol", "up_s": 3204}]
    assert page["ogn"] == {"connected": {"sbs": True}}
    assert len(page["containers"]) == 2
    del BODIES["http://ogn-bridge:8080/"]
    assert status.status()["ogn"] is None  # one source down: null, page still served
    BODIES["http://socket-proxy:2375/containers/json?all=1"] = ROWS[1:]
    assert status.status()["containers"] is None  # our own container not listed

# The HTTP endpoint serves status() as uncached JSON.
srv = ThreadingHTTPServer(("127.0.0.1", 0), status.Handler)
Thread(target=srv.serve_forever, daemon=True).start()
with (
    mock.patch.object(status, "status", return_value={"k": 1}),
    urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/status.json", timeout=5) as r,
):
    assert json.load(r) == {"k": 1}
    assert r.headers["Content-Type"] == "application/json"
    assert r.headers["Cache-Control"] == "no-store"
srv.shutdown()
print("ok")
