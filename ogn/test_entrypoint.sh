#!/bin/sh
# Checks entrypoint.sh renders the config, waits for the FLARM SDR, then execs the binary.
set -eu
cd "$(dirname "$0")"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/usr/local/share/ogn" "$tmp/ogn" "$tmp/sys/bus/usb/devices/1-1"
# shellcheck disable=SC2016 # literal: envsubst fills it in
echo 'Call = "$OGN_CALL"' > "$tmp/usr/local/share/ogn/rtlsdr-ogn.conf.template"
echo geoid > "$tmp/usr/local/share/ogn/WW15MGH.DAC"
# The binaries report how they were started; sleep plugs the SDR in, so the wait ends.
for b in ogn-rf ogn-decode; do printf '#!/bin/sh\necho "%s $*"\n' "$b" > "$tmp/bin/$b"; done
printf '#!/bin/sh\necho SER1 > "%s/sys/bus/usb/devices/1-1/serial"\n' "$tmp" > "$tmp/bin/sleep"
chmod +x "$tmp"/bin/*
run() { PATH="$tmp/bin:$PATH" OGN_TEST_ROOT=$tmp OGN_CALL=MYSTN "$@" "${SH:-sh}" ./entrypoint.sh 2>&1; }
check() {
    got=$1 want=$2
    [ "$got" = "$want" ] && return
    echo "FAIL: got '$got' want '$want'" >&2
    exit 1
}

run env -u BINARY | grep -q "BINARY: set by the image" # message wording differs by shell
check "$(run env BINARY=ogn-decode)" "ogn-decode $tmp/ogn/rtlsdr-ogn.conf"
check "$(cat "$tmp/ogn/rtlsdr-ogn.conf")" 'Call = "MYSTN"'
check "$(cat "$tmp/ogn/WW15MGH.DAC")" geoid
check "$(run env BINARY=ogn-rf SDR_FLARM_SERIAL=)" "ogn-rf $tmp/ogn/rtlsdr-ogn.conf" # no serial: no wait
check "$(run env BINARY=ogn-rf SDR_FLARM_SERIAL=SER1)" "SDR serial SER1 not attached - waiting
ogn-rf $tmp/ogn/rtlsdr-ogn.conf"
check "$(run env BINARY=ogn-rf SDR_FLARM_SERIAL=SER1)" "ogn-rf $tmp/ogn/rtlsdr-ogn.conf" # attached: straight on
echo ok
