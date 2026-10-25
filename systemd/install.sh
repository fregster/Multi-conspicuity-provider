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

sudo systemctl daemon-reload
sudo systemctl enable "${UNIT_NAME}"
sudo systemctl enable --now net-watchdog.timer

cat <<EOF
Installed and enabled ${UNIT_NAME} (starts on boot), and net-watchdog.timer.

Manage it with:
  sudo systemctl start   ${UNIT_NAME}
  sudo systemctl stop    ${UNIT_NAME}
  sudo systemctl restart ${UNIT_NAME}
  sudo systemctl status  ${UNIT_NAME}
EOF
