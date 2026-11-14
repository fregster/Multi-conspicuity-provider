"""Run: STATION_LAT=51 STATION_LON=0 python3 test_bridge.py, in the ogn-bridge image
(ogn-client, and the geoid grid asterix.py reads)."""

import contextlib
import json
import logging
import os
import queue
import socket
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from typing import Any
from unittest import mock

os.environ.setdefault("STATION_LAT", "51")
os.environ.setdefault("STATION_LON", "0")
import bridge
import test_asterix  # asterix.py's own checks, so this run covers bridge's import of it too

logging.disable(logging.CRITICAL)  # the bridge logs every connect/drop; keep test output to asserts


class StopLoopError(Exception):
    pass


def fake_sock() -> mock.Mock:
    return mock.Mock(spec=socket.socket)


sent: list[bytes] = []


def fake_readsb_send(record: bytes) -> bool:
    sent.append(record)
    return True


readsb_patch = mock.patch.object(bridge, "readsb_send", fake_readsb_send)
readsb_patch.start()
LINE = "FLRDDA5BA>APRS,TCPIP*:/074548h5111.32N/00102.04W'086/007/A=000607 !W00! id06DDA5BA -019fpm"
OTHER = "FLR123456>OGFLR,qAS,RX:/074548h5111.32N/00102.04W'086/007/A=000607 !W00! id06123456"

local_out, local_in = socket.socketpair()
net_out, net_in = socket.socketpair()
bridge.outputs[True].add(local_out)
bridge.outputs[False].add(net_out)

assert bridge.process_beacon(LINE, local=False)  # network only: shown
assert len(sent) == 1
assert net_in.recv(4096) == sent[0]  # stream output: network port only
local_in.setblocking(False)
try:
    local_in.recv(4096)
    raise AssertionError("network beacon reached the local-only output")
except BlockingIOError:
    pass
local_in.setblocking(True)
bridge.process_beacon(LINE, local=True)  # local: shown
assert local_in.recv(4096) == sent[1]
bridge.process_beacon(LINE, local=False)  # network copy of a locally-heard aircraft: dropped
assert len(sent) == 2
local_in.close()  # a reader that went away is dropped, not retried
bridge.broadcast(b"x", True)
assert local_out not in bridge.outputs[True]
bridge.process_beacon(OTHER, local=False)  # different aircraft, network only: shown
assert len(sent) == 3
bridge.last_local["DDA5BA"] -= bridge.LOCAL_HOLD_S + 1  # local went quiet: network resumes
bridge.process_beacon(LINE, local=False)
assert len(sent) == 4
assert bridge.process_beacon("MYCALL>APRS,TCPIP*:/074548h5111.32NI00102.04W&/A=000607", local=True)  # receiver
assert bridge.process_beacon("garbage", local=True)  # not ours to judge: still relayed
assert len(sent) == 4
assert {"local_to_tar1090", "network_to_tar1090"} <= bridge.last.keys()  # status page ages
assert 2 not in test_asterix.items(sent[0])  # FLARM address: no I021/040, readsb's non-ICAO "~"
# ICAO address: I021/040 ATP 0, so readsb merges it with the aircraft's own ADS-B entry
bridge.process_beacon(
    "ICA4080F6>OGADSB,qAS,RX:/074548h5111.32N/00102.04W^152/335/A=015413 !W00! id054080F6", local=False
)
assert test_asterix.items(sent[-1])[2] == b"\x10"
assert test_asterix.items(sent[-1])[11] == bytes.fromhex("4080F6")

# Privacy flags (idSTttttaa), each case local and network: no-tracking (T) alone decides,
# stealth (S) changes nothing. Withheld beacons are counted, not stored or sent anywhere.
flag_lines = {
    (stealth, no_track): f"FLR{a}>OGFLR,qAS,RX:/074548h5111.32N/00102.04W'086/007/A=000607 !W00! id{f}{a}"
    for (stealth, no_track), (f, a) in {
        (False, False): ("06", "A00001"),
        (True, False): ("86", "A00002"),
        (False, True): ("46", "A00003"),
        (True, True): ("C6", "A00004"),
    }.items()
}
for local in (False, True):  # network first: a local copy would hold the network one back
    for (stealth, no_track), line in flag_lines.items():
        n_sent, n_withheld = len(sent), bridge.withheld["no_tracking"]
        assert bridge.process_beacon(line, local=local) is not no_track, (stealth, no_track)
        assert len(sent) == n_sent + (not no_track)
        assert bridge.withheld["no_tracking"] == n_withheld + no_track
assert {"A00003", "A00004"}.isdisjoint(bridge.last_local)
assert {"A00001", "A00002"} <= bridge.last_local.keys()

# Beacons per protocol and source: ogn-client's beacon_type, ADS-L by its dstcall.
bridge.process_beacon(
    "ADS123456>OGADSL,qAS,RX:/074548h5111.32N/00102.04W'000/000/A=001000 !W00! id25123456", local=True
)
assert bridge.heard[True]["adsl"] == 1
assert bridge.heard[True]["flarm"] == 2  # the two shown flag cases
assert bridge.heard[True]["unknown"] == 1  # LINE's dstcall, APRS, is no protocol
readsb_patch.stop()
before = dict(bridge.last)
with mock.patch.object(bridge, "readsb_send", return_value=False):  # tar1090 down: shown on the stream only
    bridge.process_beacon(OTHER, local=True)
assert bridge.last["local_to_tar1090"] == before["local_to_tar1090"]
for o in (local_out, net_out, net_in):
    bridge.outputs[True].discard(o)
    bridge.outputs[False].discard(o)
    o.close()

# readsb_send(): connect on demand, at most one attempt per 5s while ultrafeeder is down.
with mock.patch.object(socket, "create_connection", side_effect=OSError("refused")) as connect:
    assert not bridge.readsb_send(b"a")
    assert not bridge.readsb_send(b"b")  # inside the retry window: no second attempt
    assert connect.call_count == 1
    assert bridge.connected["readsb"] is False
connect.assert_called_with(("ultrafeeder", 32021), timeout=3)
bridge.readsb.retry_at = 0.0
up = fake_sock()
with mock.patch.object(socket, "create_connection", return_value=up) as connect:
    assert bridge.readsb_send(b"a")
    assert bridge.readsb_send(b"b")  # reuses the open connection
    assert connect.call_count == 1
    assert bridge.connected["readsb"] is True
up.sendall.assert_called_with(b"b")
up.sendall.side_effect = OSError
assert not bridge.readsb_send(b"c")  # dropped; the next record reconnects
assert up.close.called
assert bridge.readsb.sock is None
assert bridge.connected["readsb"] is False

# serve_output(): each stream reader is added with a 1s send timeout.
srv, reader = fake_sock(), fake_sock()
srv.accept.side_effect = [(reader, ("1.2.3.4", 5)), StopLoopError()]
with mock.patch.object(socket, "create_server", return_value=srv) as create, contextlib.suppress(StopLoopError):
    bridge.serve_output(False)
create.assert_called_with(("", 30022))
reader.settimeout.assert_called_with(1)
assert reader in bridge.outputs[False]
bridge.outputs[False].clear()

# The status page: link state and seconds since each flow last moved data.
bridge.seen("uplink_tx")
http = ThreadingHTTPServer(("127.0.0.1", 0), bridge.StatusHandler)
threading.Thread(target=http.serve_forever, daemon=True).start()
with urllib.request.urlopen(f"http://127.0.0.1:{http.server_address[1]}/", timeout=5) as r:
    page = json.load(r)
http.shutdown()
assert page["connected"] == bridge.connected
assert page["age_s"]["uplink_tx"] == 0
assert page["heard"] == {"local": bridge.heard[True], "network": bridge.heard[False]}
assert page["withheld"] == {"no_tracking": 4}


class FakeUp:
    def sendall(self, data: bytes) -> None:
        pass

    def settimeout(self, value: float) -> None:
        pass

    def recv(self, n: int) -> bytes:
        raise BlockingIOError

    def close(self) -> None:
        pass


# ogn-decode reconnects: the old relay exits after the new one is up, uplink must stay connected
with (
    mock.patch.object(socket, "create_connection", return_value=FakeUp()),
    mock.patch.object(queue.Queue, "get", side_effect=queue.Empty),  # no 5s wait per loop
):
    old, new = threading.Event(), threading.Event()
    for stop in (old, new):
        q: queue.Queue[str] = queue.Queue()
        threading.Thread(target=bridge.relay_upstream, args=(q, "user X", stop), daemon=True).start()
    time.sleep(0.1)
    old.set()
    time.sleep(0.1)
    assert bridge.connected["uplink"] == 1, bridge.connected
    new.set()
    time.sleep(0.1)
    assert bridge.connected["uplink"] == 0, bridge.connected

# relay(): sends what ogn-decode queued, drains server comments, stops when the server hangs up.
up = fake_sock()
up.recv.side_effect = [BlockingIOError, b"# server comment", b""]
fq = mock.Mock(spec=queue.Queue)
fq.get.side_effect = ["pos", queue.Empty, queue.Empty]
try:
    bridge.relay(up, fq, threading.Event())
    raise AssertionError("relay kept going after the server closed")
except bridge.ClosedByServerError as e:
    assert str(e) == "closed by server"
up.sendall.assert_called_once_with(b"pos\r\n")

# relay_upstream(): offline, the backlog is dropped (no stale replay); a lost link reconnects.
q = queue.Queue()
q.put("stale 1")
q.put("stale 2")
stop = mock.Mock(spec=threading.Event)
stop.is_set.side_effect = [False, False, True]
up = fake_sock()
uplinks = []


def lose_link(*_: object) -> None:
    uplinks.append(bridge.connected["uplink"])
    raise bridge.ClosedByServerError


with (
    mock.patch.object(socket, "create_connection", side_effect=[OSError("no route"), up]),
    mock.patch.object(bridge, "relay", lose_link),
):
    bridge.relay_upstream(q, "user X pass 1", stop)
assert q.empty()
assert uplinks == [1]
assert bridge.connected["uplink"] == 0
up.sendall.assert_called_once_with(b"user X pass 1\r\n")
up.settimeout.assert_called_once_with(0)
assert up.close.called
assert stop.wait.call_count == 2

# login(): acknowledged with ogn-decode's callsign (OGN if it sent none), relay started.
for line, call in [("user MYCALL pass 1", "MYCALL"), ("user", "OGN")]:
    conn = fake_sock()
    with mock.patch.object(bridge, "relay_upstream") as relay_upstream:
        lq = bridge.login(conn, line, threading.Event())
    deadline = time.monotonic() + 5
    while not relay_upstream.called and time.monotonic() < deadline:
        time.sleep(0.01)
    assert relay_upstream.call_args.args[0] is lq
    assert relay_upstream.call_args.args[1] == line
    conn.sendall.assert_called_once_with(f"# logresp {call} verified, server OGNBRIDGE\r\n".encode())
assert bridge.connected["decoder"] == 2
bridge.connected["decoder"] = 0

# serve_decoder(): banner (again as keepalive when quiet), login, then one position per
# line however recv splits them. A line goes upstream only if process_beacon allows it
# (not no-tracking), and a full relay queue drops it for the network only.
full_q: queue.Queue[str] = queue.Queue(maxsize=1)
logins: list[tuple[str, threading.Event]] = []


def fake_login(conn: socket.socket, line: str, stop: threading.Event) -> "queue.Queue[str]":
    logins.append((line, stop))
    bridge.connected["decoder"] += 1
    return full_q


conn = fake_sock()
conn.recv.side_effect = [
    TimeoutError,
    b"user MYCALL pass 1\n# c\n\n" + LINE.encode()[:20],
    LINE.encode()[20:] + b"\nhidden\nlate\n",
    b"",
]
with (
    mock.patch.object(bridge, "login", fake_login),
    mock.patch.object(bridge, "process_beacon", side_effect=[True, False, True]) as pb,
):
    bridge.serve_decoder(conn)
assert [line for line, _ in logins] == ["user MYCALL pass 1"]
assert pb.call_args_list == [mock.call(x, local=True) for x in (LINE, "hidden", "late")]
assert list(full_q.queue) == [LINE]  # "hidden" withheld; "late" found the queue full
assert [c.args[0][:8] for c in conn.sendall.call_args_list] == [b"# aprsc "] * 2
assert logins[0][1].is_set()
assert conn.close.called
assert bridge.connected["decoder"] == 0
conn = fake_sock()
conn.sendall.side_effect = BrokenPipeError  # gone before the banner: no login to undo
bridge.serve_decoder(conn)
assert conn.close.called
assert bridge.connected["decoder"] == 0

# The network feed: every line counts as liveness; position lines are processed as network.
with mock.patch.object(bridge, "process_beacon") as pb:
    bridge.on_network_line("x")
pb.assert_called_once_with("x", local=False)
assert "network_rx" in bridge.last
bad, good = mock.Mock(), mock.Mock()
bad.connect.side_effect = OSError("offline")
with (
    mock.patch.object(bridge, "AprsClient", side_effect=[bad, good, StopLoopError()]) as client,
    mock.patch.object(time, "sleep", side_effect=[None, StopLoopError()]) as sleep,
    contextlib.suppress(StopLoopError),
):
    bridge.run_network_feed()
client.assert_called_with(aprs_user="OGNBRIDGE", aprs_filter="r/51.0/0.0/150")
good.run.assert_called_once_with(callback=bridge.on_network_line, autoreconnect=True)
sleep.assert_called_with(30)

# main(): the network feed, both stream outputs and the status page in the background,
# then a thread per ogn-decode connection.
srv, conn = fake_sock(), fake_sock()
srv.accept.side_effect = [(conn, ("127.0.0.1", 1)), StopLoopError()]
with (
    mock.patch.object(threading, "Thread") as thread,
    mock.patch.object(bridge, "ThreadingHTTPServer") as status_server,
    mock.patch.object(socket, "create_server", return_value=srv) as create,
    contextlib.suppress(StopLoopError),
):
    bridge.main()
targets: list[Any] = [(c.kwargs["target"], c.kwargs.get("args")) for c in thread.call_args_list]
assert targets == [
    (bridge.run_network_feed, None),
    (bridge.serve_output, (True,)),
    (bridge.serve_output, (False,)),
    (status_server.return_value.serve_forever, None),
    (bridge.serve_decoder, (conn,)),
]
status_server.assert_called_once_with(("", 8080), bridge.StatusHandler)
create.assert_called_once_with(("", 14580))
print("ok")
