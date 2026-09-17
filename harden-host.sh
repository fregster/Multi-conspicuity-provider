#!/usr/bin/env bash
# Optional: applies the dockerHosting OS/Docker hardening baseline to this host.
# Run on the host itself (e.g. `ssh -t pi ~/multi-conspicuity-provider/harden-host.sh`).
# It's interactive; extra args are passed straight to dockerHosting's setup.sh
# (see `/opt/dockerHosting/setup.sh --help`), e.g. --force=firewall.
set -euo pipefail

REPO=${DOCKERHOSTING_REPO:-https://github.com/DigitalisCloudServices/dockerHosting.git}
REF=${DOCKERHOSTING_REF:-main}
DIR=${DOCKERHOSTING_DIR:-/opt/dockerHosting}

# traefik: this stack runs its own on 80/443.
# aide: daily full-disk hashing wears SD cards - drop it via HARDEN_SKIP if on SSD/NVMe.
SKIP=${HARDEN_SKIP:-traefik,aide}
# Blacklisting usb-storage leaves a USB-booted Pi unbootable.
case "$(findmnt -no SOURCE /)" in /dev/sd*) SKIP+=",usb" ;; esac

command -v git > /dev/null || { sudo apt-get update && sudo apt-get install -y git; }
if [ -d "$DIR/.git" ]; then
    sudo git -C "$DIR" fetch --quiet origin "$REF"
    sudo git -C "$DIR" checkout --quiet FETCH_HEAD
else
    sudo git clone --quiet --branch "$REF" "$REPO" "$DIR"
fi

# userns-remap=no: remapped container root can't open /dev/bus/usb, so the SDRs would vanish.
sudo "$DIR/setup.sh" --local-dir="$DIR" --skip="$SKIP" --userns-remap=no "$@"
