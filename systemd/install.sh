#!/usr/bin/env bash
# Installs and enables the systemd unit so the stack starts on boot and can
# be managed with systemctl start/stop/restart/status. Run this ON the host
# that runs the stack (e.g. via ssh, or after deploy.sh has synced the repo).
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT_DIR="$(pwd)"
UNIT_NAME=multi-conspicuity-provider.service

sed "s|__PROJECT_DIR__|${PROJECT_DIR}|" systemd/multi-conspicuity-provider.service |
  sudo tee "/etc/systemd/system/${UNIT_NAME}" >/dev/null

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
sudo /usr/sbin/ethtool --set-eee eth0 eee off 2>/dev/null || true

sudo systemctl daemon-reload
sudo systemctl enable "${UNIT_NAME}"
sudo systemctl enable --now net-watchdog.timer

cat <<EOF
Installed and enabled ${UNIT_NAME} (starts on boot), net-watchdog.timer,
a persistent journal and EEE off on eth0.

Manage it with:
  sudo systemctl start   ${UNIT_NAME}
  sudo systemctl stop    ${UNIT_NAME}
  sudo systemctl restart ${UNIT_NAME}
  sudo systemctl status  ${UNIT_NAME}
EOF
