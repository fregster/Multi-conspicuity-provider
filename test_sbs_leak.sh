#!/usr/bin/env bash
# Runs the ultrafeeder image and readsb args from docker-compose.yml, sends
# FLARM/OGN-style contacts to its SBS input the way ogn-bridge does, and fails
# if any reach its SBS output (adsbhub's feed) or if they drop off the map.
# Needs Docker; pulls the ultrafeeder image.
set -euo pipefail
cd "$(dirname "$0")"

# Dummy UUIDs: the shell env overrides .env.example's blanks.
for var in $(grep -oE '^[A-Z0-9_]+_UUID=' .env.example | tr -d =); do export "$var=x"; done
config=$(docker compose --env-file .env.example config ultrafeeder)
image=$(sed -n 's/^ *image: //p' <<< "$config")
args=$(sed -n 's/^ *READSB_EXTRA_ARGS: //p' <<< "$config")

name=sbs-leak-test-$$
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
docker run -d --name "$name" -p 127.0.0.1::32006 -p 127.0.0.1::30003 \
    -e READSB_LAT=51 -e READSB_LON=-1 -e READSB_EXTRA_ARGS="$args" "$image" > /dev/null
for _ in $(seq 90); do
    docker exec "$name" test -f /run/readsb/aircraft.json 2> /dev/null && break
    sleep 2
done
sleep 5
sbs_in=$(docker port "$name" 32006 | head -1 | cut -d: -f2)
sbs_out=$(docker port "$name" 30003 | head -1 | cut -d: -f2)

exec 4<> "/dev/tcp/127.0.0.1/$sbs_out"
exec 3<> "/dev/tcp/127.0.0.1/$sbs_in"
for _ in $(seq 10); do
    now=$(date -u +%Y/%m/%d,%H:%M:%S.000)
    # One "~" (non-ICAO) contact and one ICAO-addressed one, as bridge.py sends.
    for addr in '~DDA5BA' 4080F6; do
        printf 'MSG,3,1,1,%s,1,%s,%s,TEST,2000,80,90,51.10000,-1.00000,0,,,,,\r\n' "$addr" "$now" "$now" >&3
    done
    sleep 0.3
done
while IFS= read -r -t 3 line <&4; do
    [[ $line == MSG* ]] && {
        echo "FAIL: SBS input leaked to SBS output: $line" >&2
        exit 1
    }
done
sleep 2
map=$(docker exec "$name" cat /run/readsb/aircraft.json)
grep -q '"~dda5ba"' <<< "$map" || {
    echo "FAIL: OGN contact missing from the map" >&2
    exit 1
}
echo ok
