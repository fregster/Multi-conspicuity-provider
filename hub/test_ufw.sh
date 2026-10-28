#!/usr/bin/env bash
# Checks ufw.sh replaces its own UFW rules for the profiles in .env, with sudo stubbed.
set -euo pipefail
cd "$(dirname "$0")"
tmp=$(mktemp -d)
# ufw.sh reads the repo's .env: keep any real one safe.
env=../.env
[[ -f $env ]] && mv "$env" "$tmp/env.real"
trap '[[ -f $tmp/env.real ]] && mv "$tmp/env.real" "$env" || rm -f "$env"; rm -rf "$tmp"' EXIT
mkdir "$tmp/bin"
# shellcheck disable=SC2016 # $* belongs to the stub
printf '#!/bin/sh\necho "$*" >> "%s/calls"\ncase "$*" in "ufw status numbered") cat "%s/rules";; "ufw status") echo "30004 ALLOW # mcp-hub";; esac\n' \
    "$tmp" "$tmp" > "$tmp/bin/sudo"
chmod +x "$tmp/bin/sudo"
cat > "$tmp/rules" << 'EOF'
[ 3] 30004,30005,31090/tcp ALLOW IN Anywhere # mcp-hub
[ 4] 22/tcp ALLOW IN Anywhere
[12] 33003,33047/tcp ALLOW IN Anywhere # mcp-stream-tls
[15] 30004,30005,31090/tcp ALLOW IN 1.2.3.4 # mcp-hub
EOF
run() {
    rm -f "$tmp/calls"
    PATH="$tmp/bin:$PATH" ./ufw.sh > /dev/null 2>&1 # no .env: grep says so
    grep -v '^ufw status' "$tmp/calls" || true
}
check() {
    local got=$1 want=$2
    [[ $got == "$want" ]] && return
    echo "FAIL: got '$got' want '$want'" >&2
    exit 1
}

# No .env: no profiles, so it only removes its old rules - highest number first.
check "$(run)" "ufw --force delete 15
ufw --force delete 3
ufw --force delete 12"

cat > "$env" << 'EOF'
COMPOSE_PROFILES="hub,stream"
HUB_BEAST_IN_PORT=40004
HUB_ALLOWED_SOURCES=10.0.0.0/8,192.168.1.5
STREAM_ALLOWED_SOURCES=none
EOF
check "$(run)" "ufw --force delete 15
ufw --force delete 3
ufw allow proto tcp from 10.0.0.0/8 to any port 40004,30005,31090 comment mcp-hub
ufw allow proto tcp from 192.168.1.5 to any port 40004,30005,31090 comment mcp-hub
ufw --force delete 12"

# stream-tls defaults to anywhere (the client cert is the control); stream opens only named sources.
echo 'COMPOSE_PROFILES=stream,stream-tls
STREAM_ALLOWED_SOURCES=172.16.0.0/12' > "$env"
check "$(run)" "ufw --force delete 15
ufw --force delete 3
ufw allow proto tcp from 172.16.0.0/12 to any port 30003,30047 comment mcp-stream
ufw --force delete 12
ufw allow proto tcp from any to any port 33003,33047 comment mcp-stream-tls"
echo ok
