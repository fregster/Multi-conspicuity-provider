"""
Subscribes to the Open Glider Network's public APRS-IS feed (not our own
ogn-rf/ogn-decode - this uses every nearby OGN receiver, not just ours, and
keeps working even when ogn-rf loses the dongle race with ultrafeeder) and
re-emits each position as a synthetic SBS/BaseStation line into readsb's SBS
input port, so tar1090 renders FLARM/OGN traffic on the same map as ADS-B.

SBS line format and the "~" non-ICAO-address prefix convention are taken
from readsb's own decoder (decodeSbsLine in net_io.c), not guessed:
MSG,3,1,1,~icaoHex,1,date,time,date,time,callsign,alt_ft,speed_kt,track,lat,lon,vrate_fpm,,,,,,
"""
import os
import socket
import time
import logging
from datetime import datetime, timezone

from ogn.client import AprsClient
from ogn.parser import parse, AprsParseError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("ogn-bridge")

STATION_LAT = float(os.environ["STATION_LAT"])
STATION_LON = float(os.environ["STATION_LON"])
RANGE_KM = int(os.environ.get("OGN_RANGE_KM", "150"))
SBS_HOST = os.environ.get("SBS_HOST", "ultrafeeder")
SBS_PORT = int(os.environ.get("SBS_PORT", "32006"))

FEET_PER_METER = 1 / 0.3048
KNOTS_PER_KMH = 1 / 1.852
FPM_PER_MS = 1 / 0.00508

sbs_sock = None


def sbs_connect():
    global sbs_sock
    while True:
        try:
            sbs_sock = socket.create_connection((SBS_HOST, SBS_PORT), timeout=10)
            log.info(f"Connected to SBS input at {SBS_HOST}:{SBS_PORT}")
            return
        except OSError as e:
            log.warning(f"SBS connect failed ({e}), retrying in 5s")
            time.sleep(5)


def sbs_send(line):
    global sbs_sock
    data = (line + "\r\n").encode()
    try:
        sbs_sock.sendall(data)
    except OSError:
        log.warning("SBS connection lost, reconnecting")
        sbs_connect()
        sbs_sock.sendall(data)


def sanitize_callsign(name):
    cleaned = "".join(ch for ch in name.upper() if ch.isalnum())[:8]
    return cleaned or "OGN"


def to_sbs(beacon):
    # Only actual aircraft position reports - not receiver/weather-station beacons.
    if beacon.get("aprs_type") != "position":
        return None
    if not all(k in beacon for k in ("address", "latitude", "longitude")):
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


def process_beacon(raw_message):
    try:
        beacon = parse(raw_message)
    except AprsParseError:
        return
    line = to_sbs(beacon)
    if line:
        sbs_send(line)


def main():
    sbs_connect()
    aprs_filter = f"r/{STATION_LAT}/{STATION_LON}/{RANGE_KM}"
    client = AprsClient(aprs_user="OGNBRIDGE", aprs_filter=aprs_filter)
    client.connect()
    log.info(f"Connected to OGN APRS-IS with filter: {aprs_filter}")
    client.run(callback=process_beacon, autoreconnect=True)


if __name__ == "__main__":
    main()
