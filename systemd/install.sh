#!/usr/bin/env bash
# Installs and enables the systemd unit so the stack starts on boot and can
# be managed with systemctl start/stop/restart/status. Run this ON the host
# that runs the stack (e.g. via ssh, or after deploy.sh has synced the repo).
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT_DIR="$(pwd)"
UNIT_NAME=multi-conspicuity-provider.service

sed "s|__PROJECT_DIR__|${PROJECT_DIR}|" systemd/multi-conspicuity-provider.service |
    sudo tee "/etc/systemd/system/${UNIT_NAME}" > /dev/null

# Host-side uplink watchdog (see net-watchdog.sh). The script is copied out of
# the repo rather than run in place, so it stays root-owned.
sudo install -m 0755 systemd/net-watchdog.sh /usr/local/sbin/net-watchdog
sudo install -m 0644 systemd/net-watchdog.service systemd/net-watchdog.timer /etc/systemd/system/

# Persistent journal (see 99-persistent.conf) - flushes the RAM journal to disk.
sudo install -D -m 0644 systemd/99-persistent.conf /etc/systemd/journald.conf.d/99-persistent.conf
sudo systemctl restart systemd-journald
sudo journalctl --flush

# EEE off on eth0 at every boot (udev rule), and now. Briefly renegotiates the link.
sudo install -m 0644 systemd/90-eth0-no-eee.rules /etc/udev/rules.d/
sudo /usr/sbin/ethtool --set-eee eth0 eee off 2> /dev/null || true

# Kernel DVB-T drivers grab the RTL-SDRs at boot, starving readsb/ogn-rf.
# Offer to blacklist them unless something already does.
DVB_MODS=(dvb_usb_rtl28xxu rtl2832_sdr rtl2832 rtl2830)
ROOT=${INSTALL_TEST_ROOT:-} # test_install.sh points this at a fake /sys and /etc
if sudo modinfo -n dvb_usb_rtl28xxu > /dev/null 2>&1 &&
    ! grep -qsE '^\s*blacklist\s+dvb_usb_rtl28xxu\b' "$ROOT"/etc/modprobe.d/*.conf; then
    loaded=()
    for m in "${DVB_MODS[@]}"; do [[ -d $ROOT/sys/module/$m ]] && loaded+=("$m"); done
    echo "Kernel DVB drivers for RTL-SDRs are not blacklisted${loaded[*]:+ (loaded now: ${loaded[*]})}."
    echo "They can claim the SDRs at boot and stop ADS-B and FLARM receiving."
    read -r -p "Blacklist them in /etc/modprobe.d/blacklist-rtlsdr.conf? [Y/n] " ans || ans=n
    if [[ ${ans,,} != n* ]]; then
        sudo install -m 0644 systemd/blacklist-rtlsdr.conf /etc/modprobe.d/
        if ((${#loaded[@]})); then
            sudo modprobe -r "${loaded[@]}" || echo "Couldn't unload ${loaded[*]} - reboot to free the SDRs."
        fi
    else
        echo "Skipped - add it later with: sudo install -m 0644 systemd/blacklist-rtlsdr.conf /etc/modprobe.d/"
    fi
fi

sudo systemctl daemon-reload
sudo systemctl enable "${UNIT_NAME}"
sudo systemctl enable --now net-watchdog.timer

cat << EOF
Installed and enabled ${UNIT_NAME} (starts on boot), net-watchdog.timer,
a persistent journal and EEE off on eth0.

Manage it with:
  sudo systemctl start   ${UNIT_NAME}
  sudo systemctl stop    ${UNIT_NAME}
  sudo systemctl restart ${UNIT_NAME}
  sudo systemctl status  ${UNIT_NAME}
EOF
