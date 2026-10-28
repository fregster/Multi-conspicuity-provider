#!/bin/sh
# Checks peers.sh turns BEAST_PEERS into the right ULTRAFEEDER_CONFIG entries.
set -eu
cd "$(dirname "$0")"
run() { BEAST_PEER_UUID=u ULTRAFEEDER_CONFIG=$1 BEAST_PEERS=$2 ./peers.sh "$3" printenv ULTRAFEEDER_CONFIG 2> /dev/null; }
check() { [ "$1" = "$2" ] || {
    echo "FAIL: got '$1' want '$2'" >&2
    exit 1
}; }

peers="a,out=h1:1,in=h2:2;b,in=h3:3;c,out=h4:4"
check "$(run base "$peers" out)" "base;adsb,h1,1,beast_reduce_plus_out,uuid=u;adsb,h4,4,beast_reduce_plus_out,uuid=u"
check "$(run base "$peers" in)" "base;adsb,h2,2,beast_in,uuid=u;adsb,h3,3,beast_in,uuid=u"
check "$(run base "" out)" "base"
check "$(run "" "c,out=h4:4" out)" "adsb,h4,4,beast_reduce_plus_out,uuid=u"
# mlat= only in peer-map, reading our own SDR rather than peer-map's mixed feed
check "$(run base "d,out=h5:5,mlat=m:6" out)" "base;adsb,h5,5,beast_reduce_plus_out,uuid=u"
check "$(run base "d,out=h5:5,mlat=m:6" in)" "base;mlat,m,6,uuid=u,input_connect=ultrafeeder:30005"
echo ok
