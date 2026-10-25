#!/usr/bin/env bash
# Run every minute by net-watchdog.timer. If the host has had no IPv4 default
# route, or its gateway hasn't answered pings, for FAIL_LIMIT consecutive runs,
# re-activates the interface so NetworkManager starts a fresh DHCP exchange.
# Added after the host sat with no lease for ~28h, every container failing DNS
# with "network is unreachable". Harmless if the LAN itself is down: it just
# retries every FAIL_LIMIT minutes until it comes back.
set -uo pipefail

IFACE=${NET_WATCHDOG_IFACE:-eth0}
FAIL_LIMIT=${NET_WATCHDOG_FAIL_LIMIT:-5}
STATE=/run/net-watchdog.fails

gw=$(ip -4 route show default dev "$IFACE" | awk '{print $3; exit}')
if [[ -n "$gw" ]] && ping -c 3 -W 2 -I "$IFACE" "$gw" > /dev/null 2>&1; then
    rm -f "$STATE"
    exit 0
fi

fails=$(($(cat "$STATE" 2> /dev/null || echo 0) + 1))
if ((fails < FAIL_LIMIT)); then
    echo "$fails" > "$STATE"
    echo "${IFACE}: gateway ${gw:-<no default route>} unreachable (${fails}/${FAIL_LIMIT})"
    exit 0
fi

rm -f "$STATE"
echo "${IFACE}: gateway ${gw:-<no default route>} unreachable ${FAIL_LIMIT} times - re-activating"
nmcli device connect "$IFACE"
