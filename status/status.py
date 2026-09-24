"""
Serves /status.json for the landing page: every container in this compose
project (state + Docker healthcheck), per-aggregator ADS-B feed status from
ultrafeeder, and ogn-bridge's OGN link health. Stdlib only; each source is
fetched per request and reported as null if it can't be reached.

Container list comes via the read-only socket-proxy; only name/state/status
are passed on, never env or labels. The dedicated feeder containers' own
healthchecks already verify their uplink, so their health IS their feed health.
"""

import json
import os
import re
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

DOCKER = "http://socket-proxy:2375"
ULTRAFEEDER = "http://ultrafeeder"
BRIDGE = "http://ogn-bridge:8080/"
# "mlat,host,port,..." entries - the same ULTRAFEEDER_CONFIG ultrafeeder runs with.
MLAT = re.findall(r"(?:^|;)mlat,([^,;]+),(\d+)", os.environ.get("ULTRAFEEDER_CONFIG", ""))


def get(url):
    with urlopen(url, timeout=3) as r:
        return r.read()


def containers():
    rows = json.loads(get(f"{DOCKER}/containers/json?all=1"))
    project = next(
        c["Labels"].get("com.docker.compose.project") for c in rows if c["Id"].startswith(socket.gethostname())
    )
    return [
        {"name": c["Names"][0].lstrip("/"), "state": c["State"], "status": c["Status"]}
        for c in rows
        if c["Labels"].get("com.docker.compose.project") == project
    ]


def beast(prom):
    # readsb_net_connector_status{host="in.adsb.lol",port="30004"} 3204  -> seconds connected, 0 if down
    return [
        {"host": h, "up_s": int(float(v))}
        for h, v in re.findall(r'readsb_net_connector_status\{host="([^"]+)",port="\d+"\} (\S+)', prom)
    ]


def mlat():
    out = []
    for host, port in MLAT:
        try:
            s = json.loads(get(f"{ULTRAFEEDER}/mlat-client-stats/{host}:{port}.json"))
            out.append({"host": host, "peers": s.get("peer_count"), "age_s": round(time.time() - s["now"])})
        except OSError:
            out.append({"host": host, "peers": None, "age_s": None})  # no stats file: not (yet) synced
    return out


def safe(fn):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - one unreachable source must not blank the whole page
        return None


def status():
    return {
        "containers": safe(containers),
        "beast": safe(lambda: beast(get(f"{ULTRAFEEDER}/metrics").decode())),
        "mlat": safe(mlat),
        "ogn": safe(lambda: json.loads(get(BRIDGE))),
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(status()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # silence per-request stderr logging


if __name__ == "__main__":
    ThreadingHTTPServer(("", 8080), Handler).serve_forever()
