"""Run: STATION_LAT=51 STATION_LON=0 python3 test_bridge.py  (needs ogn-client)"""

import os

os.environ.setdefault("STATION_LAT", "51")
os.environ.setdefault("STATION_LON", "0")
import bridge

sent = []
bridge.sbs_send = lambda line: sent.append(line) or True
LINE = "FLRDDA5BA>APRS,TCPIP*:/074548h5111.32N/00102.04W'086/007/A=000607 !W00! id06DDA5BA -019fpm"
OTHER = "FLR123456>OGFLR,qAS,RX:/074548h5111.32N/00102.04W'086/007/A=000607 !W00! id06123456"

bridge.process_beacon(LINE, local=False)  # network only: shown
assert len(sent) == 1
bridge.process_beacon(LINE, local=True)  # local: shown
bridge.process_beacon(LINE, local=False)  # network copy of a locally-heard aircraft: dropped
assert len(sent) == 2
bridge.process_beacon(OTHER, local=False)  # different aircraft, network only: shown
assert len(sent) == 3
bridge.last_local["DDA5BA"] -= bridge.LOCAL_HOLD_S + 1  # local went quiet: network resumes
bridge.process_beacon(LINE, local=False)
assert len(sent) == 4
bridge.process_beacon("MYCALL>APRS,TCPIP*:/074548h5111.32NI00102.04W&/A=000607", local=True)  # receiver beacon
bridge.process_beacon("garbage", local=True)
assert len(sent) == 4
assert {"local_to_tar1090", "network_to_tar1090"} <= bridge.last.keys()  # status page ages


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
