#!/usr/bin/env bash
# Gives every *_UUID listed in .env.example a value in .env: existing values
# are kept, missing or blank ones get a fresh random UUID.
set -euo pipefail
cd "$(dirname "$0")"

[[ -f .env ]] || {
    echo ".env missing - copy .env.example and fill it in" >&2
    exit 1
}
# Make sure appended lines don't get glued onto an unterminated last line.
[[ -z "$(tail -c1 .env)" ]] || echo >> .env

for var in $(grep -oE '^[A-Z0-9_]+_UUID=' .env.example | tr -d =); do
    grep -qE "^${var}=.+" .env && continue
    uuid=$(cat /proc/sys/kernel/random/uuid 2> /dev/null || uuidgen | tr '[:upper:]' '[:lower:]')
    if grep -qE "^${var}=" .env; then
        sed -i.bak "s/^${var}=.*/${var}=${uuid}/" .env && rm -f .env.bak
    else
        echo "${var}=${uuid}" >> .env
    fi
    echo "generated ${var}"
done
