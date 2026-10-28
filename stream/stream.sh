#!/bin/sh
# Entrypoint for the stream container: one net-only readsb that merges our own
# station with the STREAM_PROVIDERS picked in .env. readsb keeps one state per
# aircraft however many inputs report it, so the outputs are de-duplicated:
# SBS/BaseStation on 30003 and JSON positions on 30047, both thinned to one
# update per aircraft per STREAM_INTERVAL_S. stream-gate / stream-tls-* publish them.
#   STREAM_PROVIDERS  blank = local only: our 1090 SDR and our OGN receiver.
#                     Otherwise a comma list of extra sources, or "all":
#                       mlat  aggregators' MLAT results for our receiver
#                       ogn   the OGN network beyond our own receiver
#                       <name> a BEAST_PEERS peer's in= feed
set -eu
providers=",${STREAM_PROVIDERS:-},"
want() {
    name=$1
    case $providers in
        *,all,* | *,"$name",*) return 0 ;;
        *) return 1 ;;
    esac
}

set -- --net-connector=ultrafeeder,30005,beast_in --net-connector=ogn-bridge,30003,sbs_in
if want mlat; then set -- "$@" --net-connector=ultrafeeder,31005,beast_in; fi
if want ogn; then set -- "$@" --net-connector=ogn-bridge,30013,sbs_in; fi
IFS=';'
for peer in ${BEAST_PEERS:-}; do
    want "${peer%%,*}" || continue
    IFS=','
    for field in $peer; do
        case $field in
            in=*:*)
                hostport=${field#in=}
                set -- "$@" "--net-connector=${hostport%:*},${hostport##*:},beast_in"
                ;;
            *) ;; # out= and mlat= feeds aren't streamed
        esac
    done
    IFS=';'
done
unset IFS
echo "stream sources: $*" >&2

interval=${STREAM_INTERVAL_S:-0.5}
exec "${READSB:-/usr/local/bin/readsb}" --net-only --quiet \
    --lat "${STATION_LAT:?}" --lon "${STATION_LON:?}" \
    --forward-mlat --forward-mlat-sbs \
    --net-sbs-port=30003 --net-sbs-reduce --net-beast-reduce-interval="$interval" \
    --net-json-port=30047 --net-json-port-interval="$interval" \
    "$@"
