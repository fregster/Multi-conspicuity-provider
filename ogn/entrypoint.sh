#!/bin/sh
set -e
: "${BINARY:?set by the image}"
ROOT=${OGN_TEST_ROOT:-} # test_entrypoint.sh points this at a fake filesystem
# ogn-rf falls back to device #0 when DeviceSerial isn't attached, which is the
# 1090 dongle - and it then holds it so ultrafeeder can't. Wait for ours instead.
if [ "$BINARY" = ogn-rf ] && [ -n "$SDR_FLARM_SERIAL" ]; then
    until grep -qsx "$SDR_FLARM_SERIAL" "$ROOT"/sys/bus/usb/devices/*/serial; do
        echo "SDR serial $SDR_FLARM_SERIAL not attached - waiting" >&2
        sleep 30
    done
fi
envsubst < "$ROOT"/usr/local/share/ogn/rtlsdr-ogn.conf.template > "$ROOT"/ogn/rtlsdr-ogn.conf
cp "$ROOT"/usr/local/share/ogn/WW15MGH.DAC "$ROOT"/ogn/ # skips ogn-decode's startup download
exec "$BINARY" "$ROOT"/ogn/rtlsdr-ogn.conf
