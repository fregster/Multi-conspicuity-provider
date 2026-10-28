#!/usr/bin/env bash
# Checks net-watchdog.sh's escalation ladder with ip/ping/nmcli/systemctl/sleep stubbed.
set -euo pipefail
cd "$(dirname "$0")"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/bin"
for c in nmcli systemctl sleep; do printf '#!/bin/sh\necho "%s $*" >> "%s/calls"\n' "$c" "$tmp" > "$tmp/bin/$c"; done
# shellcheck disable=SC2016 # $2/$* belong to the stub, not this shell
# "ip route" prints a gateway; "ip link" is logged. ping succeeds only while $tmp/up exists.
printf '#!/bin/sh\ncase "$2" in route) echo "default via 10.0.0.1";; *) echo "ip $*" >> "%s/calls";; esac\n' "$tmp" > "$tmp/bin/ip"
printf '#!/bin/sh\n[ -f "%s/up" ]\n' "$tmp" > "$tmp/bin/ping"
chmod +x "$tmp"/bin/*

run() { PATH="$tmp/bin:$PATH" NET_WATCHDOG_STATE_DIR=$tmp ./net-watchdog.sh > /dev/null; }
action() { tail -n1 "$tmp/calls" 2> /dev/null | cut -d' ' -f1-3; }
check() { [[ "$1" == "$2" ]] || { echo "FAIL at run $i: got '$1' want '$2'" >&2; exit 1; }; }

# Never-reachable gateway: re-activate at 5,10, bounce at 15,20,25,30 - no reboot.
for i in $(seq 1 35); do
    run
    case $i in
        5 | 10) check "$(action)" "nmcli device connect" ;;
        15 | 20 | 25 | 30 | 35) check "$(action)" "ip link set" ;;
    esac
done

# Gateway answered earlier this boot: reboot once the failures reach 30.
rm -f "$tmp"/calls "$tmp"/net-watchdog.fails
touch "$tmp/up"; run; rm "$tmp/up"
for i in $(seq 1 30); do run; done
check "$(action)" "systemctl reboot"
echo ok
