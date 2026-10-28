#!/usr/bin/env bash
# Checks install.sh's units, files and DVB-blacklist prompt with sudo stubbed and a
# fake /sys and /etc (INSTALL_TEST_ROOT).
set -euo pipefail
cd "$(dirname "$0")"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/root/etc/modprobe.d" "$tmp/root/sys/module"
# sudo logs each command. "tee" keeps what it's given; modinfo finds the DVB
# driver while $tmp/dvb exists; modprobe fails while $tmp/busy exists.
# shellcheck disable=SC2016 # $1/$* belong to the stub
printf '#!/bin/sh
echo "$*" >> "%s/calls"
case "$1" in
    tee) cat > "%s/unit" ;;
    modinfo) [ -f "%s/dvb" ] ;;
    modprobe) [ ! -f "%s/busy" ] ;;
esac\n' "$tmp" "$tmp" "$tmp" "$tmp" > "$tmp/bin/sudo"
chmod +x "$tmp/bin/sudo"
# run ANSWER: install.sh's output, its prompt answered with ANSWER (none: stdin closed).
run() {
    local answer=$1
    rm -f "$tmp/calls"
    if [[ $answer == none ]]; then
        PATH="$tmp/bin:$PATH" INSTALL_TEST_ROOT=$tmp/root ./install.sh < /dev/null
    else
        PATH="$tmp/bin:$PATH" INSTALL_TEST_ROOT=$tmp/root ./install.sh <<< "$answer"
    fi
}
did() {
    local cmd=$1
    grep -qxF -- "$cmd" "$tmp/calls" && return
    echo "FAIL: no 'sudo $cmd' in: $(cat "$tmp/calls")" >&2
    exit 1
}
didnt() {
    local cmd=$1
    grep -q -- "$cmd" "$tmp/calls" || return 0
    echo "FAIL: unexpected '$cmd' in: $(cat "$tmp/calls")" >&2
    exit 1
}
says() {
    local out=$1 want=$2
    grep -qF -- "$want" <<< "$out" && return
    echo "FAIL: no '$want' in output: $out" >&2
    exit 1
}
repo=$(cd .. && pwd)
blacklist="install -m 0644 systemd/blacklist-rtlsdr.conf /etc/modprobe.d/"

# No DVB driver on this kernel: no prompt; everything else installed and enabled.
out=$(run none)
grep -qxF "WorkingDirectory=$repo" "$tmp/unit" # __PROJECT_DIR__ filled in
did "tee /etc/systemd/system/multi-conspicuity-provider.service"
did "install -m 0755 systemd/net-watchdog.sh /usr/local/sbin/net-watchdog"
did "journalctl --flush"
did "systemctl daemon-reload"
did "systemctl enable multi-conspicuity-provider.service"
did "systemctl enable --now net-watchdog.timer"
didnt "$blacklist"
says "$out" "sudo systemctl status  multi-conspicuity-provider.service"

# Driver present and loaded: "y" blacklists and unloads it.
touch "$tmp/dvb"
mkdir "$tmp/root/sys/module/rtl2832" "$tmp/root/sys/module/dvb_usb_rtl28xxu"
out=$(run y)
says "$out" "not blacklisted (loaded now: dvb_usb_rtl28xxu rtl2832)"
did "$blacklist"
did "modprobe -r dvb_usb_rtl28xxu rtl2832"

# Still in use: says to reboot.
touch "$tmp/busy"
says "$(run Y)" "Couldn't unload dvb_usb_rtl28xxu rtl2832 - reboot to free the SDRs."

# Not loaded: blacklisted, nothing to unload.
rm -r "$tmp/root/sys/module/"*
says "$(run "")" "not blacklisted." # Enter = yes
did "$blacklist"
didnt "modprobe -r"

# "n", or no answer at all: skipped, with the command for later.
for answer in n none; do
    says "$(run $answer)" "Skipped - add it later with: sudo $blacklist"
    didnt "$blacklist"
done

# Already blacklisted: no prompt.
echo "blacklist dvb_usb_rtl28xxu" > "$tmp/root/etc/modprobe.d/other.conf"
out=$(run none)
grep -q "not blacklisted" <<< "$out" && exit 1
didnt "$blacklist"
echo ok
