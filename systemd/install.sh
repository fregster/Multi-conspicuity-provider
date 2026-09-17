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

sudo systemctl daemon-reload
sudo systemctl enable "${UNIT_NAME}"

cat <<EOF
Installed and enabled ${UNIT_NAME} (starts on boot).

Manage it with:
  sudo systemctl start   ${UNIT_NAME}
  sudo systemctl stop    ${UNIT_NAME}
  sudo systemctl restart ${UNIT_NAME}
  sudo systemctl status  ${UNIT_NAME}
EOF
