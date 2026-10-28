"""Run: python3 test_healer.py"""

import os
import socket

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
print("ok")
