#!/bin/sh
set -e
envsubst < /usr/local/share/ogn/rtlsdr-ogn.conf.template > /ogn/rtlsdr-ogn.conf
cp /usr/local/share/ogn/WW15MGH.DAC /ogn/ # skips ogn-decode's startup download
exec "$BINARY" /ogn/rtlsdr-ogn.conf
