#!/usr/bin/env bash
# Run every minute by net-watchdog.timer. If the host has had no IPv4 default
# route, or its gateway hasn't answered pings, for FAIL_LIMIT consecutive runs,
# escalates every FAIL_LIMIT runs until it answers again:
#   1st, 2nd: re-activate the interface (fresh DHCP exchange from NetworkManager)
#   3rd on:   bounce the link (resets the NIC/PHY)
#   after REBOOT_AFTER runs: reboot - only if the gateway answered earlier this
#             boot, so a site with no LAN at all never reboot-loops.
# Added after the host sat with no lease for ~28h, and again after eth0 stopped
# passing traffic for 3h20m (2026-10-28) through 37 re-activations - only a
# power cycle fixed that.
set -uo pipefail

IFACE=${NET_WATCHDOG_IFACE:-eth0}
FAIL_LIMIT=${NET_WATCHDOG_FAIL_LIMIT:-5}
REBOOT_AFTER=${NET_WATCHDOG_REBOOT_AFTER:-30}
STATE_DIR=${NET_WATCHDOG_STATE_DIR:-/run}
STATE=$STATE_DIR/net-watchdog.fails
SEEN_OK=$STATE_DIR/net-watchdog.seen-ok

gw=$(ip -4 route show default dev "$IFACE" | awk '{print $3; exit}')
if [[ -n "$gw" ]] && ping -c 3 -W 2 -I "$IFACE" "$gw" > /dev/null 2>&1; then
    rm -f "$STATE"
    touch "$SEEN_OK"
    exit 0
fi

fails=$(($(cat "$STATE" 2> /dev/null || echo 0) + 1))
echo "$fails" > "$STATE"
msg="${IFACE}: gateway ${gw:-<no default route>} unreachable (${fails} runs)"

if ((fails >= REBOOT_AFTER)) && [[ -f "$SEEN_OK" ]]; then
    echo "$msg - rebooting"
    systemctl reboot
elif ((fails % FAIL_LIMIT)); then
    echo "$msg"
elif ((fails / FAIL_LIMIT <= 2)); then
    echo "$msg - re-activating"
    nmcli device connect "$IFACE"
else
    echo "$msg - bouncing link"
    ip link set "$IFACE" down
    sleep 5
    ip link set "$IFACE" up
fi
