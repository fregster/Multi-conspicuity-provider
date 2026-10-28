#!/bin/sh
# Checks stream.sh picks readsb inputs from STREAM_PROVIDERS and BEAST_PEERS.
set -eu
cd "$(dirname "$0")"
run() { READSB=echo STATION_LAT=1 STATION_LON=2 STREAM_PROVIDERS=$1 BEAST_PEERS=$2 "${SH:-sh}" ./stream.sh 2> /dev/null | tr ' ' '\n' | grep connector= | sed 's/--net-connector=//' | tr '\n' ' '; }
check() { [ "$1" = "$2" ] || {
    echo "FAIL: got '$1' want '$2'" >&2
    exit 1
}; }

local="ultrafeeder,30005,beast_in ogn-bridge,30003,sbs_in "
peers="a,out=h1:1,in=h2:2;b,in=h3:3;c,out=h4:4"
check "$(run "" "$peers")" "$local"
check "$(run b "$peers")" "${local}h3,3,beast_in "
check "$(run "mlat,a" "$peers")" "${local}ultrafeeder,31005,beast_in h2,2,beast_in "
check "$(run ogn "$peers")" "${local}ogn-bridge,30013,sbs_in "
check "$(run all "$peers")" "${local}ultrafeeder,31005,beast_in ogn-bridge,30013,sbs_in h2,2,beast_in h3,3,beast_in "
check "$(run nope "")" "$local"
echo ok
