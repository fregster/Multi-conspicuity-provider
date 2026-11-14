#!/usr/bin/env bash
# Runs the ultrafeeder image and readsb args from docker-compose.yml and sends
# it FLARM/OGN contacts: CAT021 records from ogn-bridge's own encoder
# (asterix.py) on the ASTERIX input, as ogn-bridge sends them, and SBS lines on
# the SBS input it still opens. Fails if any reach a network output (SBS:
# adsbhub's feed; Beast and Beast-reduce: piaware, fr24feed, the ultrafeeder
# aggregators...; JSON), or if they drop off the map or land there as anything
# but non-ADS-B traffic with a geometric height.
# Needs Docker; pulls the ultrafeeder image and builds ogn-bridge's.
set -euo pipefail
cd "$(dirname "$0")"

# Dummy UUIDs: the shell env overrides .env.example's blanks.
for var in $(grep -oE '^[A-Z0-9_]+_UUID=' .env.example | tr -d =); do export "$var=x"; done
config=$(docker compose --env-file .env.example config ultrafeeder)
image=$(sed -n 's/^ *image: //p' <<< "$config")
args=$(sed -n 's/^ *READSB_EXTRA_ARGS: //p' <<< "$config")
bridge=$(docker build -q ogn-bridge)

name=sbs-leak-test-$$
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
docker run -d --name "$name" -e READSB_LAT=51 -e READSB_LON=-1 -e READSB_EXTRA_ARGS="$args" "$image" > /dev/null
for _ in $(seq 90); do
    docker exec "$name" test -f /run/readsb/aircraft.json 2> /dev/null && break
    sleep 2
done
sleep 5

# In ultrafeeder's network namespace: listen on the outputs, send the contacts,
# then check nothing came out and what the map shows.
docker run --rm -i --network "container:$name" -w / --entrypoint python3 "$bridge" - << 'EOF'
import json, socket, sys, time, urllib.request
from datetime import UTC, datetime

import asterix

def fail(why):
    sys.exit(f"FAIL: {why}")

outputs = {port: socket.create_connection(("127.0.0.1", port)) for port in (30003, 30005, 30006, 30047)}
sbs_in = socket.create_connection(("127.0.0.1", 32006))
asterix_in = socket.create_connection(("127.0.0.1", 32021))
glider = {"aprs_type": "position", "aircraft_type": 1, "name": "TEST", "latitude": 51.1, "longitude": -1.0,
          "altitude": 600.0, "ground_speed": 150.0, "track": 90, "climb_rate": 1.0}
for _ in range(10):
    stamp = datetime.now(UTC).strftime("%Y/%m/%d,%H:%M:%S.000")
    for addr in ("~DDA5BA", "4080F6"):  # one non-ICAO ("~") and one ICAO address, each way in
        sbs_in.sendall(f"MSG,3,1,1,{addr},1,{stamp},{stamp},TEST,2000,80,90,51.10000,-1.00000,0,,,,,\r\n".encode())
    for addr, address_type in (("DDA5BB", 2), ("4080F7", asterix.ADDRESS_TYPE_ICAO)):
        asterix_in.sendall(asterix.to_cat021({**glider, "address": addr, "address_type": address_type}, datetime.now(UTC)))
    time.sleep(0.3)
time.sleep(3)
for port, conn in outputs.items():
    conn.setblocking(False)
    data = b""
    try:
        while chunk := conn.recv(65536):
            data += chunk
    except BlockingIOError:
        pass
    data = data.replace(b"\x1a1" + bytes(9), b"")  # Beast heartbeats carry no aircraft
    if b"MSG" in data or b"{" in data or data.startswith(b"\x1a"):
        fail(f"FLARM/OGN contacts leaked to output {port}: {data[:60]!r}")

with urllib.request.urlopen("http://127.0.0.1/data/aircraft.json") as r:
    seen = {a["hex"]: a for a in json.load(r)["aircraft"]}
if "~dda5ba" not in seen:
    fail("SBS contact missing from the map")
# What readsb makes of the CAT021 items (decodeAsterixMessage, net_io.c): heights and rates in 6.25ft steps.
want = {
    "type": "unknown",  # not adsb_icao: never listed as real ADS-B
    "alt_baro": None,  # no barometric altitude made up from GNSS
    "alt_geom": int(round((600 + asterix.geoid_m(51.1, -1.0)) / 0.3048 / 6.25) * 6.25),
    "geom_rate": int(round(1.0 / 0.00508 / 6.25) * 6.25),
    "category": "B1",
}
for hex_ in ("~dda5bb", "4080f7"):  # the ICAO one keeps its plain address
    got = {k: seen.get(hex_, {}).get(k) for k in want}
    if got != want:
        fail(f"ASTERIX contact {hex_} on the map as {got}, want {want}")
EOF
echo ok
