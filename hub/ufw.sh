#!/usr/bin/env bash
# Opens the public ports of the profiles in COMPOSE_PROFILES in UFW. Run on the
# host after changing COMPOSE_PROFILES or the values below in .env: it replaces
# the rules it added before (comments "mcp-hub", "mcp-stream", "mcp-stream-tls").
#   hub         hub-gate's ports, from HUB_ALLOWED_SOURCES (default anywhere)
#   stream      plain stream ports, from STREAM_ALLOWED_SOURCES (default none:
#               no auth on these, so name your LAN/project IPs explicitly)
#   stream-tls  mTLS stream ports, from STREAM_TLS_ALLOWED_SOURCES (default
#               anywhere: the client cert is the control)
# Plain INPUT rules are enough because the gates use the host network; a
# Docker-published port would bypass UFW entirely (DNAT -> FORWARD ->
# DOCKER-FORWARD accepts it before any ufw chain).
set -euo pipefail
cd "$(dirname "$0")/.."
PATH="$PATH:/usr/sbin:/sbin"

# Read single values rather than sourcing .env (see deploy.sh).
env_val() {
  local v
  v=$(grep -m1 "^$1=" .env | cut -d= -f2-) || true
  v="${v%\"}"; v="${v#\"}"
  echo "${v:-$2}"
}
profiles=",$(env_val COMPOSE_PROFILES ""),"

# open TAG PROFILE PORTS SOURCES - SOURCES "none" opens nothing.
open() {
  # Highest rule number first, so deleting doesn't renumber the rest.
  sudo ufw status numbered | sed -n "s/^\[ *\([0-9]*\)\].*# $1 *\$/\1/p" | sort -rn |
    while read -r n; do sudo ufw --force delete "$n" > /dev/null; done
  [[ $profiles == *",$2,"* && $4 != none ]] || return 0
  for src in ${4//,/ }; do
    sudo ufw allow proto tcp from "$src" to any port "$3" comment "$1"
  done
}
open mcp-hub hub "$(env_val HUB_BEAST_IN_PORT 30004),$(env_val HUB_BEAST_OUT_PORT 30005),$(env_val HUB_MLAT_PORT 31090)" \
  "$(env_val HUB_ALLOWED_SOURCES any)"
open mcp-stream stream "$(env_val STREAM_SBS_PORT 30003),$(env_val STREAM_JSON_PORT 30047)" \
  "$(env_val STREAM_ALLOWED_SOURCES none)"
open mcp-stream-tls stream-tls "$(env_val STREAM_TLS_SBS_PORT 33003),$(env_val STREAM_TLS_JSON_PORT 33047)" \
  "$(env_val STREAM_TLS_ALLOWED_SOURCES any)"
sudo ufw status | grep mcp- || true
