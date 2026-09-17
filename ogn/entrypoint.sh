#!/bin/sh
set -e
envsubst < /ogn/rtlsdr-ogn.conf.template > /ogn/rtlsdr-ogn.conf
exec "$@"
