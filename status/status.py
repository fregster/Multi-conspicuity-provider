"""
Serves /status.json for the landing page: every container in this compose
project (state + Docker healthcheck), per-aggregator ADS-B feed status from
ultrafeeder, and ogn-bridge's OGN link health. Stdlib only; each source is
fetched per request and reported as null if it can't be reached.

Container list comes via the read-only socket-proxy; only name/state/status
are passed on, never env or labels. The dedicated feeder containers' own
healthchecks already verify their uplink, so their health IS their feed health.

The aggregators with a public "what do you see from my IP" JSON API are also
asked whether our Beast/MLAT feeds actually arrive (cached REMOTE_TTL_S, they
are third-party services). Matching is by source IP, not UUID.
"""

import functools
import json
import os
import re
import socket
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.request import Request, urlopen

DOCKER = "http://socket-proxy:2375"
ULTRAFEEDER = "http://ultrafeeder"
BRIDGE = "http://ogn-bridge:8080/"
# "mlat,host,port,..." entries - the same ULTRAFEEDER_CONFIG ultrafeeder runs with.
MLAT = re.findall(r"(?:^|;)mlat,([^,;]+),(\d+)", os.environ.get("ULTRAFEEDER_CONFIG", ""))
# site -> (url, beast list key path, mlat list key path) in that site's JSON.
# Not adsb.lol: its /0/me listed no clients while we were demonstrably feeding it.
REMOTE = {
    "adsb.fi": ("https://api.adsb.fi/v1/myip", ("beast",), ("mlat",)),
    "airplanes.live": ("https://api.airplanes.live/feed-status", ("beast_clients",), ("mlat_clients",)),
}
REMOTE_TTL_S = 300
_remote: dict[str, Any] = {"at": float("-inf"), "data": None}


def get(url: str) -> bytes:
    # Aggregator APIs 403 Python's default User-Agent.
    with urlopen(Request(url, headers={"User-Agent": "multi-conspicuity-provider/status"}), timeout=3) as r:
        data: bytes = r.read()
        return data


def containers() -> list[dict[str, str]]:
    rows = json.loads(get(f"{DOCKER}/containers/json?all=1"))
    project = next(
        c["Labels"].get("com.docker.compose.project") for c in rows if c["Id"].startswith(socket.gethostname())
    )
    return [
        {"name": c["Names"][0].lstrip("/"), "state": c["State"], "status": c["Status"]}
        for c in rows
        if c["Labels"].get("com.docker.compose.project") == project
    ]


def beast(prom: str) -> list[dict[str, Any]]:
    # readsb_net_connector_status{host="in.adsb.lol",port="30004"} 3204  -> seconds connected, 0 if down
    return [
        {"host": h, "up_s": int(float(v))}
        for h, v in re.findall(r'readsb_net_connector_status\{host="([^"]+)",port="\d+"\} (\S+)', prom)
    ]


def mlat() -> list[dict[str, Any]]:
    out = []
    for host, port in MLAT:
        try:
            s = json.loads(get(f"{ULTRAFEEDER}/mlat-client-stats/{host}:{port}.json"))
            out.append({"host": host, "peers": s.get("peer_count"), "age_s": round(time.time() - s["now"])})
        except OSError:
            out.append({"host": host, "peers": None, "age_s": None})  # no stats file: not (yet) synced
    return out


def dig(doc: Any, path: tuple[str, ...]) -> bool | None:
    """Non-empty client list at path -> True, empty -> False, key missing (API changed) -> None."""
    for k in path:
        doc = doc.get(k) if isinstance(doc, dict) else None
    return None if doc is None else bool(doc)


def seen(doc: Any, beast_path: tuple[str, ...], mlat_path: tuple[str, ...]) -> dict[str, bool | None]:
    """Does the aggregator report at least one Beast / MLAT client from our IP?"""
    return {"beast": dig(doc, beast_path), "mlat": dig(doc, mlat_path)}


def ask(url: str, beast_path: tuple[str, ...], mlat_path: tuple[str, ...]) -> dict[str, bool | None]:
    return seen(json.loads(get(url)), beast_path, mlat_path)


def remote() -> list[dict[str, Any]]:
    if time.monotonic() - _remote["at"] < REMOTE_TTL_S:
        cached: list[dict[str, Any]] = _remote["data"]
        return cached
    out = []
    for site, (url, b, m) in REMOTE.items():
        r = safe(functools.partial(ask, url, b, m)) or {"beast": None, "mlat": None}
        out.append({"site": site, **r})
    _remote.update(at=time.monotonic(), data=out)
    return out


def safe[T](fn: Callable[[], T]) -> T | None:
    try:
        return fn()
    except Exception:  # noqa: BLE001 - one unreachable source must not blank the whole page
        return None


def status() -> dict[str, Any]:
    return {
        "containers": safe(containers),
        "beast": safe(lambda: beast(get(f"{ULTRAFEEDER}/metrics").decode())),
        "mlat": safe(mlat),
        "remote": safe(remote),
        "ogn": safe(lambda: json.loads(get(BRIDGE))),
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps(status()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass  # silence per-request stderr logging


if __name__ == "__main__":
    ThreadingHTTPServer(("", 8080), Handler).serve_forever()
