#!/bin/sh
# Entrypoint wrapper for the ultrafeeder image: adds BEAST_PEERS to
# ULTRAFEEDER_CONFIG, then starts the image as normal (/init).
#   $1 = out: ultrafeeder - feed our data to each peer's out= endpoint
#   $1 = in:  peer-map    - pull each peer's in= endpoint (map only, never re-fed),
#                           and run mlat-client against each mlat= server
# BEAST_PEERS = "name,out=host:port,in=host:port,mlat=host:port;..." - all optional.
# mlat-client runs in peer-map, reading our own SDR from ultrafeeder:30005, so
# its results (computed from peers' data) land in peer-map only - ultrafeeder's
# mlathub would otherwise push them into its readsb.
set -eu
dir=$1
shift

cfg=${ULTRAFEEDER_CONFIG:-}
oldifs=$IFS
IFS=';'
for peer in ${BEAST_PEERS:-}; do
    IFS=','
    for field in $peer; do
        case $field in
            "$dir"=*:*)
                hostport=${field#*=}
                if [ "$dir" = out ]; then proto=beast_reduce_plus_out; else proto=beast_in; fi
                entry="adsb,${hostport%:*},${hostport##*:},$proto,uuid=${BEAST_PEER_UUID:?}"
                cfg=${cfg:+$cfg;}$entry
                echo "beast peer ($dir): $entry" >&2
                ;;
            mlat=*:*)
                [ "$dir" = in ] || continue
                hostport=${field#*=}
                entry="mlat,${hostport%:*},${hostport##*:},uuid=${BEAST_PEER_UUID:?},input_connect=ultrafeeder:30005"
                cfg=${cfg:+$cfg;}$entry
                echo "mlat peer: $entry" >&2
                ;;
            *) ;; # the peer name, or its feed in the other direction
        esac
    done
done
IFS=$oldifs

export ULTRAFEEDER_CONFIG="$cfg"
exec "$@"
