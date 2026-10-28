#!/usr/bin/env bash
# Opens the hub's ports in UFW, from anywhere or only HUB_ALLOWED_SOURCES
# (comma-separated IPs/CIDRs). Run on the host after setting
# COMPOSE_PROFILES=hub, and again whenever those .env values change: it
# replaces the rules it added before (comment "mcp-hub").
# Plain INPUT rules are enough because hub-gate uses the host network; a
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
ports="$(env_val HUB_BEAST_IN_PORT 30004),$(env_val HUB_BEAST_OUT_PORT 30005),$(env_val HUB_MLAT_PORT 31090)"
sources=$(env_val HUB_ALLOWED_SOURCES any)

# Highest rule number first, so deleting doesn't renumber the rest.
sudo ufw status numbered | sed -n 's/^\[ *\([0-9]*\)\].*# mcp-hub *$/\1/p' | sort -rn |
  while read -r n; do sudo ufw --force delete "$n" > /dev/null; done
for src in ${sources//,/ }; do
  sudo ufw allow proto tcp from "$src" to any port "$ports" comment mcp-hub
done
sudo ufw status | grep mcp-hub
