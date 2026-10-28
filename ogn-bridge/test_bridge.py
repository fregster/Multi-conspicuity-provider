"""Run: STATION_LAT=51 STATION_LON=0 python3 test_bridge.py  (needs ogn-client)"""

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

logging.disable(logging.CRITICAL)  # the bridge logs every connect/drop; keep test output to asserts


class StopLoopError(Exception):
    pass


def fake_sock() -> mock.Mock:
    return mock.Mock(spec=socket.socket)


sent: list[str] = []


def fake_sbs_send(line: str) -> bool:
    sent.append(line)
    return True


sbs_patch = mock.patch.object(bridge, "sbs_send", fake_sbs_send)
sbs_patch.start()
LINE = "FLRDDA5BA>APRS,TCPIP*:/074548h5111.32N/00102.04W'086/007/A=000607 !W00! id06DDA5BA -019fpm"
OTHER = "FLR123456>OGFLR,qAS,RX:/074548h5111.32N/00102.04W'086/007/A=000607 !W00! id06123456"

local_out, local_in = socket.socketpair()
net_out, net_in = socket.socketpair()
bridge.outputs[True].add(local_out)
bridge.outputs[False].add(net_out)

bridge.process_beacon(LINE, local=False)  # network only: shown
assert len(sent) == 1
assert net_in.recv(4096) == (sent[0] + "\r\n").encode()  # stream output: network port only
local_in.setblocking(False)
try:
    local_in.recv(4096)
    raise AssertionError("network beacon reached the local-only output")
except BlockingIOError:
    pass
local_in.setblocking(True)
bridge.process_beacon(LINE, local=True)  # local: shown
assert local_in.recv(4096) == (sent[1] + "\r\n").encode()
bridge.process_beacon(LINE, local=False)  # network copy of a locally-heard aircraft: dropped
assert len(sent) == 2
local_in.close()  # a reader that went away is dropped, not retried
bridge.broadcast("x", True)
assert local_out not in bridge.outputs[True]
bridge.process_beacon(OTHER, local=False)  # different aircraft, network only: shown
assert len(sent) == 3
bridge.last_local["DDA5BA"] -= bridge.LOCAL_HOLD_S + 1  # local went quiet: network resumes
bridge.process_beacon(LINE, local=False)
assert len(sent) == 4
bridge.process_beacon("MYCALL>APRS,TCPIP*:/074548h5111.32NI00102.04W&/A=000607", local=True)  # receiver beacon
bridge.process_beacon("garbage", local=True)
assert len(sent) == 4
assert {"local_to_tar1090", "network_to_tar1090"} <= bridge.last.keys()  # status page ages
assert sent[0].split(",")[4] == "~DDA5BA"  # FLARM address: readsb's non-ICAO prefix
# ICAO address: no "~", so readsb merges it with the aircraft's own ADS-B entry
bridge.process_beacon(
    "ICA4080F6>OGADSB,qAS,RX:/074548h5111.32N/00102.04W^152/335/A=015413 !W00! id054080F6", local=False
)
assert sent[-1].split(",")[4] == "4080F6", sent[-1]
sbs_patch.stop()
before = dict(bridge.last)
with mock.patch.object(bridge, "sbs_send", return_value=False):  # tar1090 down: shown on the stream only
    bridge.process_beacon(OTHER, local=True)
assert bridge.last["local_to_tar1090"] == before["local_to_tar1090"]
for o in (local_out, net_out, net_in):
    bridge.outputs[True].discard(o)
    bridge.outputs[False].discard(o)
    o.close()

# to_sbs(): position reports only, with every field it can fill.
assert bridge.to_sbs({"aprs_type": "status", "address": "A", "latitude": 1, "longitude": 2}) is None
assert bridge.to_sbs({"aprs_type": "position", "address": "A", "latitude": None, "longitude": 2}) is None
full = bridge.to_sbs(
    {
        "aprs_type": "position",
        "address": "4080F6",
        "address_type": bridge.ADDRESS_TYPE_ICAO,
        "name": "g-abcd/x!",
        "latitude": 51.5,
        "longitude": -1.25,
        "altitude": 1000,
        "ground_speed": 185.2,
        "track": 90.4,
        "climb_rate": 1.016,
    }
)
assert full is not None
f = full.split(",")
assert (f[4], f[10:16], f[16]) == ("4080F6", ["GABCDX", "3281", "100", "90", "51.50000", "-1.25000"], "200")
bare = bridge.to_sbs({"aprs_type": "position", "address": "DD", "name": "!!", "latitude": 0, "longitude": 0})
assert bare is not None
assert bare.split(",")[10:14] == ["OGN", "", "", ""]  # unusable name -> OGN; unknown values left blank

# sbs_send(): connect on demand, at most one attempt per 5s while ultrafeeder is down.
with mock.patch.object(socket, "create_connection", side_effect=OSError("refused")) as connect:
    assert not bridge.sbs_send("a")
    assert not bridge.sbs_send("b")  # inside the retry window: no second attempt
    assert connect.call_count == 1 and bridge.connected["sbs"] is False
bridge.sbs.retry_at = 0.0
up = fake_sock()
with mock.patch.object(socket, "create_connection", return_value=up) as connect:
    assert bridge.sbs_send("a")
    assert bridge.sbs_send("b")  # reuses the open connection
    assert connect.call_count == 1 and bridge.connected["sbs"] is True
up.sendall.assert_called_with(b"b\r\n")
up.sendall.side_effect = OSError
assert not bridge.sbs_send("c")  # dropped; the next line reconnects
assert up.close.called and bridge.sbs.sock is None and bridge.connected["sbs"] is False

# serve_output(): each stream reader is added with a 1s send timeout.
srv, reader = fake_sock(), fake_sock()
srv.accept.side_effect = [(reader, ("1.2.3.4", 5)), StopLoopError()]
with mock.patch.object(socket, "create_server", return_value=srv) as create, contextlib.suppress(StopLoopError):
    bridge.serve_output(False)
create.assert_called_with(("", 30013))
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
assert page["connected"] == bridge.connected and page["age_s"]["uplink_tx"] == 0


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
assert q.empty() and uplinks == [1] and bridge.connected["uplink"] == 0
up.sendall.assert_called_once_with(b"user X pass 1\r\n")
up.settimeout.assert_called_once_with(0)
assert up.close.called and stop.wait.call_count == 2

# login(): acknowledged with ogn-decode's callsign (OGN if it sent none), relay started.
for line, call in [("user MYCALL pass 1", "MYCALL"), ("user", "OGN")]:
    conn = fake_sock()
    with mock.patch.object(bridge, "relay_upstream") as relay_upstream:
        lq = bridge.login(conn, line, threading.Event())
    deadline = time.monotonic() + 5
    while not relay_upstream.called and time.monotonic() < deadline:
        time.sleep(0.01)
    assert relay_upstream.call_args.args[0] is lq and relay_upstream.call_args.args[1] == line
    conn.sendall.assert_called_once_with(f"# logresp {call} verified, server OGNBRIDGE\r\n".encode())
assert bridge.connected["decoder"] == 2
bridge.connected["decoder"] = 0

# serve_decoder(): banner (again as keepalive when quiet), login, then one position per
# line however recv splits them; a full relay queue drops the line for the network only.
full_q: queue.Queue[str] = queue.Queue(maxsize=1)
full_q.put("x")
logins: list[tuple[str, threading.Event]] = []


def fake_login(conn: socket.socket, line: str, stop: threading.Event) -> "queue.Queue[str]":
    logins.append((line, stop))
    bridge.connected["decoder"] += 1
    return full_q


conn = fake_sock()
conn.recv.side_effect = [
    TimeoutError,
    b"user MYCALL pass 1\n# c\n\n" + LINE.encode()[:20],
    LINE.encode()[20:] + b"\n",
    b"",
]
with (
    mock.patch.object(bridge, "login", fake_login),
    mock.patch.object(bridge, "process_beacon") as pb,
):
    bridge.serve_decoder(conn)
assert [line for line, _ in logins] == ["user MYCALL pass 1"]
pb.assert_called_once_with(LINE, local=True)
assert [c.args[0][:8] for c in conn.sendall.call_args_list] == [b"# aprsc "] * 2
assert logins[0][1].is_set() and conn.close.called and bridge.connected["decoder"] == 0
conn = fake_sock()
conn.sendall.side_effect = BrokenPipeError  # gone before the banner: no login to undo
bridge.serve_decoder(conn)
assert conn.close.called and bridge.connected["decoder"] == 0

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
