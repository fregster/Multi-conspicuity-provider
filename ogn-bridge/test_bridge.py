"""Run: STATION_LAT=51 STATION_LON=0 python3 test_bridge.py  (needs ogn-client)"""

import os
import queue
import socket
import threading
import time
from unittest import mock

os.environ.setdefault("STATION_LAT", "51")
os.environ.setdefault("STATION_LON", "0")
import bridge

sent: list[str] = []


def fake_sbs_send(line: str) -> bool:
    sent.append(line)
    return True


mock.patch.object(bridge, "sbs_send", fake_sbs_send).start()
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
mock.patch.object(socket, "create_connection", return_value=FakeUp()).start()
mock.patch.object(queue.Queue, "get", side_effect=queue.Empty).start()  # no 5s wait per loop
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
print("ok")
