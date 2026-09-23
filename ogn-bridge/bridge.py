"""
Puts FLARM/OGN traffic on tar1090 from two sources, re-emitted as synthetic
SBS/BaseStation lines into readsb's SBS input port:

  local    ogn-decode is pointed at this process as its "APRS server" (a minimal
           APRS-IS endpoint), so positions from our own SDR arrive here with no
           internet needed. Each line is also relayed to the real OGN network
           whenever it is reachable, so feeding OGN still works exactly as before.
  network  the public APRS-IS feed, for aircraft our dongle can't hear. Best-effort:
           if there is no internet this just retries in the background.

De-dup: our SDR is always fresher and more reliable than the network copy, so once
an aircraft has been heard locally, network reports for it are dropped for
LOCAL_HOLD_S seconds (otherwise the two sources make the icon jump between them).

SBS line format and the "~" non-ICAO-address prefix convention are taken
from readsb's own decoder (decodeSbsLine in net_io.c), not guessed:
MSG,3,1,1,~icaoHex,1,date,time,date,time,callsign,alt_ft,speed_kt,track,lat,lon,vrate_fpm,,,,,,
"""
import logging
import os
import queue
import socket
import threading
import time
from datetime import datetime, timezone

from ogn.client import AprsClient
from ogn.parser import parse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("ogn-bridge")

STATION_LAT = float(os.environ["STATION_LAT"])
STATION_LON = float(os.environ["STATION_LON"])
RANGE_KM = int(os.environ.get("OGN_RANGE_KM", "150"))
SBS_HOST = os.environ.get("SBS_HOST", "ultrafeeder")
SBS_PORT = int(os.environ.get("SBS_PORT", "32006"))
LISTEN_PORT = int(os.environ.get("APRS_LISTEN_PORT", "14580"))
UPSTREAM = ("aprs.glidernet.org", 14580)
LOCAL_HOLD_S = 30

FEET_PER_METER = 1 / 0.3048
KNOTS_PER_KMH = 1 / 1.852
FPM_PER_MS = 1 / 0.00508

sbs_sock = None
sbs_lock = threading.Lock()
sbs_retry_at = 0.0


def sbs_send(line):
    # Never blocks the decoder path for long: one short connect attempt per 5s,
    # and lines are dropped (live positions go stale anyway) while ultrafeeder is down.
    global sbs_sock, sbs_retry_at
    with sbs_lock:
        if sbs_sock is None:
            if time.monotonic() < sbs_retry_at:
                return
            try:
                sbs_sock = socket.create_connection((SBS_HOST, SBS_PORT), timeout=3)
                log.info(f"Connected to SBS input at {SBS_HOST}:{SBS_PORT}")
            except OSError as e:
                sbs_retry_at = time.monotonic() + 5
                log.warning(f"SBS connect failed ({e}), retrying in 5s")
                return
        try:
            sbs_sock.sendall((line + "\r\n").encode())
        except OSError:
            log.warning("SBS connection lost, reconnecting")
            sbs_sock.close()
            sbs_sock = None


def sanitize_callsign(name):
    cleaned = "".join(ch for ch in name.upper() if ch.isalnum())[:8]
    return cleaned or "OGN"


def to_sbs(beacon):
    # Only actual aircraft position reports - not receiver/weather-station beacons.
    if beacon.get("aprs_type") != "position":
        return None
    if not all(beacon.get(k) is not None for k in ("address", "latitude", "longitude")):
        return None

    icao = f"~{beacon['address']}"
    callsign = sanitize_callsign(beacon.get("name", "OGN"))

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y/%m/%d")
    time_str = now.strftime("%H:%M:%S.%f")[:-3]

    alt_ft = f"{beacon['altitude'] * FEET_PER_METER:.0f}" if "altitude" in beacon else ""
    speed_kt = f"{beacon['ground_speed'] * KNOTS_PER_KMH:.0f}" if "ground_speed" in beacon else ""
    track = f"{beacon['track']:.0f}" if "track" in beacon else ""
    vrate_fpm = f"{beacon['climb_rate'] * FPM_PER_MS:.0f}" if "climb_rate" in beacon else ""

    fields = [
        "MSG", "3", "1", "1", icao, "1",
        date_str, time_str, date_str, time_str,
        callsign, alt_ft, speed_kt, track,
        f"{beacon['latitude']:.5f}", f"{beacon['longitude']:.5f}",
        vrate_fpm, "", "", "", "", "",
    ]
    return ",".join(fields)


last_local = {}  # address -> monotonic time last heard by our SDR (one float per aircraft seen)


def process_beacon(raw_message, local):
    try:
        beacon = parse(raw_message)
    except Exception:  # AprsParseError, or anything odd in a line from the wire
        return
    line = to_sbs(beacon)
    if not line:
        return
    addr, now = beacon["address"], time.monotonic()
    if local:
        last_local[addr] = now
    elif now - last_local.get(addr, -LOCAL_HOLD_S) < LOCAL_HOLD_S:
        return
    sbs_send(line)


def relay_upstream(q, login, stop):
    """Forward what ogn-decode sends to the real OGN network; reconnects forever.
    Lines queued while offline are dropped, not replayed as stale positions.
    Exits when its ogn-decode connection ends (stop), so reconnects don't stack up."""
    while not stop.is_set():
        try:
            up = socket.create_connection(UPSTREAM, timeout=10)
            up.sendall((login + "\r\n").encode())
            up.setblocking(False)
            log.info("Connected to OGN network, relaying local feed")
            while not stop.is_set():
                try:
                    line = q.get(timeout=5)
                except queue.Empty:
                    line = None
                if line:
                    up.sendall((line + "\r\n").encode())
                try:  # drain server comments so its send buffer never stalls; b"" = closed
                    if not up.recv(4096):
                        raise OSError("closed by server")
                except BlockingIOError:
                    pass
            up.close()
        except OSError as e:
            log.warning(f"OGN network relay down ({e}), retrying in 10s")
            while not q.empty():  # drop the backlog
                q.get_nowait()
            stop.wait(10)


def serve_decoder(conn):
    """Minimal APRS-IS server side for ogn-decode: banner, login ack, keepalive
    comments (it treats a silent server as dead), then one position per line."""
    def comment(text):
        conn.sendall(f"# {text}\r\n".encode())

    def banner():
        return "aprsc 2.1.15-bridge " + datetime.now(timezone.utc).strftime("%d %b %Y %H:%M:%S GMT") + " OGNBRIDGE"

    conn.settimeout(15)
    q, stop = None, threading.Event()
    try:
        comment(banner())
        buf = b""
        while True:
            try:
                data = conn.recv(4096)
            except socket.timeout:
                comment(banner())
                continue
            if not data:
                return
            buf += data
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                line = raw.decode(errors="replace").strip()
                if not line or line.startswith("#"):
                    continue
                if q is None:  # first line is the login
                    q = queue.Queue(maxsize=1000)
                    threading.Thread(target=relay_upstream, args=(q, line, stop), daemon=True).start()
                    call = line.split()[1] if len(line.split()) > 1 else "OGN"
                    comment(f"logresp {call} verified, server OGNBRIDGE")
                    log.info(f"ogn-decode logged in as {call}")
                    continue
                process_beacon(line, local=True)
                try:
                    q.put_nowait(line)
                except queue.Full:
                    pass
    except OSError:
        pass
    finally:
        stop.set()
        conn.close()


def run_network_feed():
    aprs_filter = f"r/{STATION_LAT}/{STATION_LON}/{RANGE_KM}"
    while True:
        try:
            client = AprsClient(aprs_user="OGNBRIDGE", aprs_filter=aprs_filter)
            client.connect()
            log.info(f"Connected to OGN APRS-IS with filter: {aprs_filter}")
            client.run(callback=lambda m: process_beacon(m, local=False), autoreconnect=True)
        except Exception as e:  # no internet is the normal case at an offline airfield
            log.warning(f"OGN network unavailable ({e}), retrying in 30s")
            time.sleep(30)


def main():
    threading.Thread(target=run_network_feed, daemon=True).start()
    srv = socket.create_server(("", LISTEN_PORT))
    log.info(f"Listening for ogn-decode on :{LISTEN_PORT}")
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=serve_decoder, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
