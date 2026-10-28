#!/usr/bin/env bash
# Checks harden-host.sh installs git if needed, clones or updates dockerHosting,
# and runs its setup.sh with the right skips - findmnt/sudo/git stubbed.
set -euo pipefail
cd "$(dirname "$0")"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/bin" "$tmp/nogit"
printf '#!/bin/sh\necho "sudo $*" >> "%s/calls"\n' "$tmp" > "$tmp/bin/sudo"
printf '#!/bin/sh\necho "%s"\n' "/dev/sda2" > "$tmp/bin/findmnt" # USB-booted Pi
chmod +x "$tmp"/bin/*
# A PATH with no git on it, for the first run.
ln -s "$(command -v bash)" "$tmp/nogit/bash"
for c in sudo findmnt; do ln -s "$tmp/bin/$c" "$tmp/nogit/$c"; done
check() { [[ "$1" == "$2" ]] || {
    echo "FAIL: got '$1' want '$2'" >&2
    exit 1
}; }
dir=$tmp/dockerHosting

# Fresh host: no git yet, nothing cloned, root on USB storage (so usb-storage stays).
PATH=$tmp/nogit DOCKERHOSTING_DIR=$dir ./harden-host.sh --force=firewall
check "$(cat "$tmp/calls")" "sudo apt-get update
sudo apt-get install -y git
sudo git clone --quiet --branch main https://github.com/DigitalisCloudServices/dockerHosting.git $dir
sudo $dir/setup.sh --local-dir=$dir --skip=traefik,aide,usb --userns-remap=no --force=firewall"

# Already cloned, SD-card root, own skip list and ref: fetch + checkout, no usb skip.
rm "$tmp/calls"
mkdir -p "$dir/.git"
printf '#!/bin/sh\necho /dev/mmcblk0p2\n' > "$tmp/bin/findmnt"
printf '#!/bin/sh\n' > "$tmp/bin/git"
chmod +x "$tmp/bin/git"
PATH=$tmp/bin:$PATH DOCKERHOSTING_DIR=$dir DOCKERHOSTING_REF=v2 HARDEN_SKIP=traefik ./harden-host.sh
check "$(cat "$tmp/calls")" "sudo git -C $dir fetch --quiet origin v2
sudo git -C $dir checkout --quiet FETCH_HEAD
sudo $dir/setup.sh --local-dir=$dir --skip=traefik --userns-remap=no"
echo ok
