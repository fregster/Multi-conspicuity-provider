#!/usr/bin/env bash
# Syncs this repo to the Pi and (re)starts the stack there.
set -euo pipefail
cd "$(dirname "$0")"

[ -f secrets/pi.env ] || { echo "secrets/pi.env missing - copy secrets/pi.env.example and fill it in" >&2; exit 1; }
[ -f .env ] || { echo ".env missing - copy .env.example and fill it in" >&2; exit 1; }

# shellcheck disable=SC1091
source secrets/pi.env

rsync -az --delete --exclude '.git' --exclude 'secrets' ./ "${PI_USER}@${PI_HOST}:~/multi-conspicuity-provider/"
ssh "${PI_USER}@${PI_HOST}" 'cd ~/multi-conspicuity-provider && docker compose up -d --build'
