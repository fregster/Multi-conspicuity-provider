#!/bin/sh
set -e
envsubst < /usr/local/share/ogn/rtlsdr-ogn.conf.template > /ogn/rtlsdr-ogn.conf
exec "$BINARY" /ogn/rtlsdr-ogn.conf
