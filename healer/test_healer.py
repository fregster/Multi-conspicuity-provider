"""Run: python3 test_healer.py"""

import contextlib
import io
import json
import os
import socket
import time
from typing import Any
from unittest import mock

os.environ["ULTRAFEEDER_CONFIG"] = "adsb,in.adsb.lol,30004,beast_reduce_plus_out,uuid=x;mlat,in.adsb.lol,31090,uuid=x"
import healer

assert healer.UPLINKS == [("in.adsb.lol", 30004)]

rows = [("a", "ultrafeeder", "local"), ("b", "fr24feed", "uplink"), ("c", "other", "bogus")]
# Offline: only the local-health container.
assert healer.pick(rows, is_online=False, last={}, now=10_000) == [("a", "ultrafeeder")]
# Online: both, but never an unknown label value.
assert healer.pick(rows, is_online=True, last={}, now=10_000) == [("a", "ultrafeeder"), ("b", "fr24feed")]
# Restarted recently: wait out MIN_GAP_S.
assert healer.pick(rows, is_online=True, last={"fr24feed": 10_000 - 60}, now=10_000) == [("a", "ultrafeeder")]
assert healer.pick(rows, is_online=True, last={"fr24feed": 10_000 - healer.MIN_GAP_S}, now=10_000) == [
    ("a", "ultrafeeder"),
    ("b", "fr24feed"),
]

srv = socket.socket()
srv.bind(("127.0.0.1", 0))
srv.listen()
assert healer.online([("127.0.0.1", 1), ("127.0.0.1", srv.getsockname()[1])])
srv.close()
assert not healer.online([("127.0.0.1", 1)])
assert not healer.online([])

# unhealthy(): the proxy's container list, filtered server-side, reduced to (id, name, autoheal label).
urls: list[Any] = []


def fake_urlopen(req: Any, timeout: float) -> io.BytesIO:
    urls.append(req)
    return io.BytesIO(json.dumps([{"Id": "c1", "Names": ["/fr24feed"], "Labels": {"autoheal": "uplink"}}]).encode())


with mock.patch.object(healer, "urlopen", fake_urlopen):
    assert healer.unhealthy() == [("c1", "fr24feed", "uplink")]
    assert urls[-1].startswith("http://socket-proxy:2375/containers/json?filters=")
    assert "unhealthy" in urls[-1]
    healer.restart("c1")
    assert (urls[-1].get_method(), urls[-1].full_url) == (
        "POST",
        "http://socket-proxy-restart:2375/containers/c1/restart?t=30",
    )


# main(): one scripted round per sleep, then stop.
class StopLoopError(Exception):
    pass


LOCAL, UPLINK = [("a", "ultrafeeder", "local")], [("b", "fr24feed", "uplink")]
rounds = [LOCAL, UPLINK, UPLINK, OSError("proxy down"), UPLINK]
restarted: list[str] = []
out = io.StringIO()
with (
    mock.patch.object(healer, "unhealthy", side_effect=rounds),
    mock.patch.object(healer, "online", side_effect=[False, False, True]),
    mock.patch.object(healer, "restart", restarted.append),
    mock.patch.object(time, "sleep", side_effect=[None] * (len(rounds) - 1) + [StopLoopError()]),
    contextlib.redirect_stdout(out),
    contextlib.suppress(StopLoopError),
):
    healer.main()
log = out.getvalue()
assert restarted == ["a", "b"], restarted  # local always; uplink only once back online
assert log.count("internet unreachable") == 1, log  # logged once per outage, not every round
assert "check failed: proxy down" in log
assert "ultrafeeder unhealthy - restarting" in log
print("ok")
