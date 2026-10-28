#!/usr/bin/env bash
# Checks deploy.sh's .env checks and the rsync/ssh it runs, with both stubbed.
set -euo pipefail
cd "$(dirname "$0")"
tmp=$(mktemp -d)
# It reads (and init-uuids.sh edits) the repo's .env: keep any real one safe.
[[ -f .env ]] && mv .env "$tmp/env.real"
trap '[[ -f $tmp/env.real ]] && mv "$tmp/env.real" .env || rm -f .env; rm -rf "$tmp"' EXIT
mkdir "$tmp/bin"
for c in rsync ssh; do printf '#!/bin/sh\necho "%s $*" >> "%s/calls"\n' "$c" "$tmp" > "$tmp/bin/$c"; done
chmod +x "$tmp"/bin/*
fail() {
    echo "FAIL: $*" >&2
    exit 1
}
run() { PATH="$tmp/bin:$PATH" ./deploy.sh > /dev/null; } # just its errors, via 2>&1

out=$(run 2>&1) && fail "ran without .env"
[[ $out == ".env missing - copy .env.example and fill it in" ]] || fail "$out"

echo 'PI_HOST="pi.local"' > .env
out=$(run 2>&1) && fail "ran without PI_USER"
[[ $out == "PI_HOST/PI_USER not set in .env" ]] || fail "$out"
[[ ! -e $tmp/calls ]] || fail "synced anyway"

# Quotes (either kind) are stripped; init-uuids.sh has filled the UUIDs first.
echo "PI_USER='pi'" >> .env
run
grep -q '^ADSBLOL_UUID=.' .env || fail "init-uuids.sh not run"
[[ $(cat "$tmp/calls") == "rsync -az --delete --exclude .git ./ pi@pi.local:~/multi-conspicuity-provider/
ssh pi@pi.local cd ~/multi-conspicuity-provider && docker compose up -d --build" ]] || fail "$(cat "$tmp/calls")"
echo ok
