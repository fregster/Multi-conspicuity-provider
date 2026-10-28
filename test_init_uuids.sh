#!/usr/bin/env bash
# Checks init-uuids.sh fills every *_UUID from .env.example, keeping existing values.
set -euo pipefail
cd "$(dirname "$0")"
tmp=$(mktemp -d)
# It edits the repo's .env: keep any real one safe.
[[ -f .env ]] && mv .env "$tmp/env.real"
trap '[[ -f $tmp/env.real ]] && mv "$tmp/env.real" .env || rm -f .env; rm -rf "$tmp"' EXIT
fail() {
    echo "FAIL: $*" >&2
    exit 1
}
uuid='[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
mapfile -t vars < <(grep -oE '^[A-Z0-9_]+_UUID' .env.example)

# No .env: refuses.
out=$(./init-uuids.sh 2>&1) && fail "ran without .env"
[[ $out == ".env missing - copy .env.example and fill it in" ]] || fail "$out"

# One kept, one blank, the rest missing - and a last line with no newline.
printf '%s=keep-me\n%s=\nOTHER=1' "${vars[0]}" "${vars[1]}" > .env
out=$(./init-uuids.sh)
[[ $(wc -l <<< "$out") -eq $((${#vars[@]} - 1)) ]] || fail "generated: $out"
grep -qx "${vars[0]}=keep-me" .env || fail "existing value replaced"
grep -qx "OTHER=1" .env || fail "unterminated last line was mangled"
for v in "${vars[@]:1}"; do
    [[ $(grep -cE "^$v=$uuid\$" .env) -eq 1 ]] || fail "$v not set once: $(grep "^$v=" .env)"
done
[[ ! -e .env.bak ]] || fail "sed backup left behind"

# Second run: nothing left to do, nothing changed.
cp .env "$tmp/before"
[[ -z $(./init-uuids.sh) ]] || fail "regenerated on second run"
cmp -s .env "$tmp/before" || fail ".env changed on second run"
echo ok
