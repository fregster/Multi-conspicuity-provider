#!/bin/sh
set -e
# ogn-rf falls back to device #0 when DeviceSerial isn't attached, which is the
# 1090 dongle - and it then holds it so ultrafeeder can't. Wait for ours instead.
if [ "$BINARY" = ogn-rf ] && [ -n "$SDR_FLARM_SERIAL" ]; then
  until grep -qsx "$SDR_FLARM_SERIAL" /sys/bus/usb/devices/*/serial; do
    echo "SDR serial $SDR_FLARM_SERIAL not attached - waiting" >&2
    sleep 30
  done
fi
envsubst < /usr/local/share/ogn/rtlsdr-ogn.conf.template > /ogn/rtlsdr-ogn.conf
cp /usr/local/share/ogn/WW15MGH.DAC /ogn/ # skips ogn-decode's startup download
exec "$BINARY" /ogn/rtlsdr-ogn.conf
