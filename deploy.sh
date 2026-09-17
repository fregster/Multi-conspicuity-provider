#!/usr/bin/env bash
# Syncs this repo to the Pi and (re)starts the stack there.
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] || { echo ".env missing - copy .env.example and fill it in" >&2; exit 1; }

# Extract just these two values rather than sourcing the whole file - .env
# holds real secrets that may contain characters unsafe for bash to parse.
# Strips a matching pair of surrounding quotes, since that's a common .env
# convention (our own .env.example doesn't use them, but a user's edit might).
strip_quotes() { local v="$1"; v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"; echo "$v"; }
PI_HOST=$(strip_quotes "$(grep -m1 '^PI_HOST=' .env | cut -d= -f2-)")
PI_USER=$(strip_quotes "$(grep -m1 '^PI_USER=' .env | cut -d= -f2-)")
[ -n "$PI_HOST" ] && [ -n "$PI_USER" ] || { echo "PI_HOST/PI_USER not set in .env" >&2; exit 1; }

rsync -az --delete --exclude '.git' ./ "${PI_USER}@${PI_HOST}:~/multi-conspicuity-provider/"
ssh "${PI_USER}@${PI_HOST}" 'cd ~/multi-conspicuity-provider && docker compose up -d --build'
