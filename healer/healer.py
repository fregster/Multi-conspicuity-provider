"""
Restarts containers that Docker reports as unhealthy - Docker itself never does,
`restart: unless-stopped` only covers a process that exits. Opt-in per container
with an `autoheal` label:

  autoheal=local   healthcheck only looks at the container itself (ultrafeeder:
                   readsb/nginx/tar1090 alive) - always restart when unhealthy.
  autoheal=uplink  healthcheck needs the aggregator's server (the feeders) -
                   only restart while the internet is reachable. Offline, every
                   feeder goes unhealthy and a restart can't help; it would just
                   bounce them every ~40min through an outage.

"Online" means a TCP connect to any of ultrafeeder's own Beast feed hosts
succeeds (same ULTRAFEEDER_CONFIG it runs with), which also proves DNS works.
Each container is restarted at most once per MIN_GAP_S.

Listing goes via the read-only socket-proxy; restarts via a second proxy that
allows nothing but POST /containers/<id>/(stop|restart|kill). Stdlib only.
"""

import json
import os
import re
import socket
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

LIST = "http://socket-proxy:2375"
RESTART = "http://socket-proxy-restart:2375"
INTERVAL_S = int(os.environ.get("HEALER_INTERVAL_S", "60"))
MIN_GAP_S = int(os.environ.get("HEALER_MIN_GAP_S", "1800"))
# "adsb,host,port,..." entries - the aggregators ultrafeeder feeds.
UPLINKS = [(h, int(p)) for h, p in re.findall(r"(?:^|;)adsb,([^,;]+),(\d+)", os.environ.get("ULTRAFEEDER_CONFIG", ""))]
FILTERS = quote(json.dumps({"health": ["unhealthy"], "label": ["autoheal"]}))


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def unhealthy():
    with urlopen(f"{LIST}/containers/json?filters={FILTERS}", timeout=10) as r:
        return [(c["Id"], c["Names"][0].lstrip("/"), c["Labels"].get("autoheal")) for c in json.load(r)]


def online(uplinks=UPLINKS):
    for host, port in uplinks:
        try:
            socket.create_connection((host, port), timeout=5).close()
            return True
        except OSError:
            continue
    return False


def restart(cid):
    urlopen(Request(f"{RESTART}/containers/{cid}/restart?t=30", method="POST"), timeout=90).close()


def pick(rows, is_online, last, now):
    """Which (id, name) rows to restart this round."""
    return [
        (cid, name)
        for cid, name, mode in rows
        if mode in ("local", "uplink")
        and (mode == "local" or is_online)
        and now - last.get(name, float("-inf")) >= MIN_GAP_S
    ]


def main():
    last = {}
    offline_logged = False
    while True:
        try:
            rows = unhealthy()
            is_online = online() if any(mode == "uplink" for *_, mode in rows) else True
            if not is_online and not offline_logged:
                log("internet unreachable - holding off restarting uplink feeders: " + ", ".join(n for _, n, _ in rows))
            offline_logged = not is_online
            for cid, name in pick(rows, is_online, last, time.monotonic()):
                log(f"{name} unhealthy - restarting")
                last[name] = time.monotonic()
                restart(cid)
        except Exception as e:  # noqa: BLE001 - keep looping through proxy/daemon hiccups
            log(f"check failed: {e}")
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
