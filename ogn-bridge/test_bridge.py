"""Run: STATION_LAT=51 STATION_LON=0 python3 test_bridge.py  (needs ogn-client)"""

import os

os.environ.setdefault("STATION_LAT", "51")
os.environ.setdefault("STATION_LON", "0")
import bridge

sent = []
bridge.sbs_send = lambda line: sent.append(line) or True
LINE = "FLRDDA5BA>APRS,TCPIP*:/074548h5111.32N/00102.04W'086/007/A=000607 !W00! id06DDA5BA -019fpm"
OTHER = "FLR123456>OGFLR,qAS,RX:/074548h5111.32N/00102.04W'086/007/A=000607 !W00! id06123456"

local_out, local_in = bridge.socket.socketpair()
net_out, net_in = bridge.socket.socketpair()
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
    def sendall(self, data):
        pass

    def setblocking(self, flag):
        pass

    def recv(self, n):
        raise BlockingIOError

    def close(self):
        pass


# ogn-decode reconnects: the old relay exits after the new one is up, uplink must stay connected
bridge.socket.create_connection = lambda *a, **k: FakeUp()
bridge.queue.Queue.get = lambda self, timeout: None  # no 5s wait per loop
old, new = bridge.threading.Event(), bridge.threading.Event()
for stop in (old, new):
    bridge.threading.Thread(
        target=bridge.relay_upstream, args=(bridge.queue.Queue(), "user X", stop), daemon=True
    ).start()
bridge.time.sleep(0.1)
old.set()
bridge.time.sleep(0.1)
assert bridge.connected["uplink"] == 1, bridge.connected
new.set()
bridge.time.sleep(0.1)
assert bridge.connected["uplink"] == 0, bridge.connected
print("ok")
