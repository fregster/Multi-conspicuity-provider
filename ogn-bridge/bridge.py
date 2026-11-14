"""
Puts FLARM/OGN traffic on tar1090 from two sources, re-emitted as ASTERIX
CAT021 records (asterix.py) into readsb's ASTERIX input port:

  local    ogn-decode is pointed at this process as its "APRS server" (a minimal
           APRS-IS endpoint), so positions from our own SDR arrive here with no
           internet needed. Each line is also relayed to the real OGN network
           whenever it is reachable, so feeding OGN still works exactly as before.
  network  the public APRS-IS feed, for aircraft our dongle can't hear. Best-effort:
           if there is no internet this just retries in the background.

De-dup: our SDR is always fresher and more reliable than the network copy, so once
an aircraft has been heard locally, network reports for it are dropped for
LOCAL_HOLD_S seconds (otherwise the two sources make the icon jump between them).

The id field's privacy flags (idSTttttaa: S stealth, T no-tracking):
  no-tracking  the pilot opted out. Shown nowhere, and not relayed to the OGN
               network either: OGN ground stations reject such data and "no
               messages with the no-tracking flag set should ever appear on the
               public APRS network" (OGN's ogn-flavoured-aprs wiki page).
               Only counted, never logged.
  stealth      FLARM's competition mode: other FLARM units get no altitude or
               climb rate, collision warnings are unaffected, and it is not an
               opt-out from being seen. Shown, with nothing beyond what the
               beacon itself carries.
"""

import contextlib
import json
import logging
import os
import queue
import socket
import threading
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import asterix
from ogn.client import AprsClient
from ogn.parser import parse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("ogn-bridge")

STATION_LAT = float(os.environ["STATION_LAT"])
STATION_LON = float(os.environ["STATION_LON"])
RANGE_KM = int(os.environ.get("OGN_RANGE_KM", "150"))
READSB_HOST = os.environ.get("READSB_HOST", "ultrafeeder")
READSB_PORT = int(os.environ.get("READSB_PORT", "32021"))  # its --net-asterix-in-port
LISTEN_PORT = int(os.environ.get("APRS_LISTEN_PORT", "14580"))
STATUS_PORT = int(os.environ.get("STATUS_PORT", "8080"))
UPSTREAM = ("aprs.glidernet.org", 14580)
LOCAL_HOLD_S = 30
# ASTERIX output ports for the stream container (its readsb connects with
# asterix_in), split so it can take our receiver without the OGN network: local? -> port.
OUT_PORTS = {True: 30021, False: 30022}
# Protocol of a beacon: ogn-client's beacon_type, which has no name for ADS-L.
DSTCALL_PROTOCOL = {"OGADSL": "adsl"}


@dataclass
class ReadsbLink:
    sock: socket.socket | None = None
    retry_at: float = 0.0


readsb = ReadsbLink()
readsb_lock = threading.Lock()

# Health for the status page: open links, and when each flow last moved data.
connected: dict[str, int | bool] = {"decoder": 0, "uplink": 0, "readsb": False}
last: dict[str, float] = {}  # event -> monotonic time
# Aircraft beacons per source (local?) and protocol, and no-tracking beacons withheld.
heard: dict[bool, Counter[str]] = {True: Counter(), False: Counter()}
withheld = {"no_tracking": 0}


def seen(event: str) -> None:
    last[event] = time.monotonic()


class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        now = time.monotonic()
        body = json.dumps(
            {
                "connected": connected,
                "age_s": {k: round(now - v) for k, v in last.items()},
                "heard": {"local": heard[True], "network": heard[False]},
                "withheld": withheld,
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass  # silence per-request stderr logging


def readsb_send(record: bytes) -> bool:
    # Never blocks the decoder path for long: one short connect attempt per 5s,
    # and records are dropped (live positions go stale anyway) while ultrafeeder is down.
    with readsb_lock:
        if readsb.sock is None:
            if time.monotonic() < readsb.retry_at:
                return False
            try:
                readsb.sock = socket.create_connection((READSB_HOST, READSB_PORT), timeout=3)
            except OSError as e:
                readsb.retry_at = time.monotonic() + 5
                connected["readsb"] = False
                log.warning("ASTERIX connect failed (%s), retrying in 5s", e)
                return False
            connected["readsb"] = True
            log.info("Connected to ASTERIX input at %s:%s", READSB_HOST, READSB_PORT)
        try:
            readsb.sock.sendall(record)
        except OSError:
            log.warning("ASTERIX connection lost, reconnecting")
            readsb.sock.close()
            readsb.sock = None
            connected["readsb"] = False
            return False
        return True


outputs: dict[bool, set[socket.socket]] = {True: set(), False: set()}  # local? -> connected readers


def serve_output(local: bool) -> None:  # noqa: FBT001  # the key of OUT_PORTS
    srv = socket.create_server(("", OUT_PORTS[local]))
    while True:
        conn, _ = srv.accept()
        conn.settimeout(1)  # a stalled reader holds up the decoder path for 1s at most, then is dropped
        outputs[local].add(conn)


def broadcast(record: bytes, local: bool) -> None:  # noqa: FBT001  # the key of outputs
    for conn in list(outputs[local]):
        try:
            conn.sendall(record)
        except OSError:
            outputs[local].discard(conn)
            conn.close()


last_local: dict[str, float] = {}  # address -> monotonic time last heard by our SDR (one float per aircraft seen)


def process_beacon(raw_message: str, *, local: bool) -> bool:
    """Shows an aircraft beacon on tar1090 and the stream. False: a no-tracking
    beacon, which must not be relayed to the OGN network either."""
    try:
        beacon = parse(raw_message)
    except Exception:  # noqa: BLE001  # AprsParseError or anything odd in a line from the wire
        return True
    if beacon.get("no-tracking"):
        withheld["no_tracking"] += 1
        return False
    record = asterix.to_cat021(beacon, datetime.now(UTC))
    if record is None:
        return True
    addr, now = beacon["address"], time.monotonic()
    heard[local][DSTCALL_PROTOCOL.get(beacon.get("dstcall", ""), beacon.get("beacon_type", "unknown"))] += 1
    if local:
        last_local[addr] = now
    elif now - last_local.get(addr, -LOCAL_HOLD_S) < LOCAL_HOLD_S:
        return True
    broadcast(record, local)
    if readsb_send(record):
        seen("local_to_tar1090" if local else "network_to_tar1090")
    return True


class ClosedByServerError(ConnectionError):
    def __str__(self) -> str:
        return "closed by server"


def relay(up: socket.socket, q: "queue.Queue[str]", stop: threading.Event) -> None:
    """Send queued lines upstream until stop; OSError once the server goes away."""
    while not stop.is_set():
        with contextlib.suppress(queue.Empty):
            up.sendall((q.get(timeout=5) + "\r\n").encode())
            seen("uplink_tx")
        # Drain server comments so its send buffer never stalls; b"" = closed.
        with contextlib.suppress(BlockingIOError):
            if not up.recv(4096):
                raise ClosedByServerError


def relay_upstream(q: "queue.Queue[str]", login: str, stop: threading.Event) -> None:
    """Forward what ogn-decode sends to the real OGN network; reconnects forever.
    Lines queued while offline are dropped, not replayed as stale positions.
    Exits when its ogn-decode connection ends (stop), so reconnects don't stack up."""
    while not stop.is_set():
        try:
            up = socket.create_connection(UPSTREAM, timeout=10)
            up.sendall((login + "\r\n").encode())
            up.settimeout(0)  # non-blocking
            log.info("Connected to OGN network, relaying local feed")
            # A count, not a bool: when ogn-decode reconnects, the old relay exits up to
            # 5s after the new one connects and would otherwise clear its flag.
            connected["uplink"] += 1
            try:
                relay(up, q, stop)
            finally:
                up.close()
                connected["uplink"] -= 1
        except OSError as e:
            log.warning("OGN network relay down (%s), retrying in 10s", e)
            while not q.empty():  # drop the backlog
                q.get_nowait()
            stop.wait(10)


def banner() -> str:
    return "aprsc 2.1.15-bridge " + datetime.now(UTC).strftime("%d %b %Y %H:%M:%S GMT") + " OGNBRIDGE"


def comment(conn: socket.socket, text: str) -> None:
    conn.sendall(f"# {text}\r\n".encode())


def decoder_lines(conn: socket.socket) -> Iterator[str]:
    """ogn-decode's non-comment lines until it disconnects, sending the banner as
    a keepalive comment whenever it goes quiet (it treats a silent server as dead)."""
    buf = b""
    while True:
        try:
            data = conn.recv(4096)
        except TimeoutError:
            comment(conn, banner())
            continue
        if not data:
            return
        buf += data
        seen("decoder_rx")
        *lines, buf = buf.split(b"\n")
        for raw in lines:
            line = raw.decode(errors="replace").strip()
            if line and not line.startswith("#"):
                yield line


def login(conn: socket.socket, line: str, stop: threading.Event) -> "queue.Queue[str]":
    """Acknowledge ogn-decode's login line and start relaying to the real OGN network."""
    q: queue.Queue[str] = queue.Queue(maxsize=1000)
    threading.Thread(target=relay_upstream, args=(q, line, stop), daemon=True).start()
    call = line.split()[1] if len(line.split()) > 1 else "OGN"
    comment(conn, f"logresp {call} verified, server OGNBRIDGE")
    log.info("ogn-decode logged in as %s", call)
    connected["decoder"] += 1
    return q


def serve_decoder(conn: socket.socket) -> None:
    """Minimal APRS-IS server side for ogn-decode: banner, login ack, keepalive
    comments, then one position per line."""
    conn.settimeout(15)
    q, stop = None, threading.Event()
    try:
        comment(conn, banner())
        for line in decoder_lines(conn):
            if q is None:  # first line is the login
                q = login(conn, line, stop)
                continue
            if process_beacon(line, local=True):
                with contextlib.suppress(queue.Full):
                    q.put_nowait(line)
    except OSError:
        pass
    finally:
        if q is not None:
            connected["decoder"] -= 1
        stop.set()
        conn.close()


def on_network_line(raw_message: str) -> None:
    seen("network_rx")  # includes the server's ~20s keepalive comments, so it's a liveness signal
    process_beacon(raw_message, local=False)


def run_network_feed() -> None:
    aprs_filter = f"r/{STATION_LAT}/{STATION_LON}/{RANGE_KM}"
    while True:
        try:
            client = AprsClient(aprs_user="OGNBRIDGE", aprs_filter=aprs_filter)
            client.connect()
            log.info("Connected to OGN APRS-IS with filter: %s", aprs_filter)
            client.run(callback=on_network_line, autoreconnect=True)
        except Exception as e:  # noqa: BLE001  # no internet is the normal case at an offline airfield
            log.warning("OGN network unavailable (%s), retrying in 30s", e)
            time.sleep(30)


def main() -> None:
    threading.Thread(target=run_network_feed, daemon=True).start()
    for local in OUT_PORTS:
        threading.Thread(target=serve_output, args=(local,), daemon=True).start()
    threading.Thread(target=ThreadingHTTPServer(("", STATUS_PORT), StatusHandler).serve_forever, daemon=True).start()
    srv = socket.create_server(("", LISTEN_PORT))
    log.info("Listening for ogn-decode on :%s", LISTEN_PORT)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=serve_decoder, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
