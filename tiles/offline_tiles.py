"""
Fills tar1090's offline basemap store (OFFLINE_TILES_DIR, mounted into
ultrafeeder) with {z}/{x}/{y}.png tiles: the whole world up to --global-zoom,
plus more detail inside --bbox up to --bbox-zoom. Run on the host, e.g. a box
around the station to zoom 10:

  sudo python3 tiles/offline_tiles.py --bbox -2.0,51.0,0.0,52.0

then `docker compose restart ultrafeeder`; tar1090's layer menu gains
"OpenStreetMap offline". Re-running adds to what's there; delete the
directory to start over.

Zooms <= 10 come from wiedehopf's prebuilt OSM tile packs (2022 data, ~2GB
download), streamed so only the tiles kept ever touch the disk. Deeper zooms
need --tile-url, a raster source whose terms allow offline/bulk download -
tile.openstreetmap.org's usage policy forbids it, so it's refused.
Stdlib only.
"""

import argparse
import math
import os
import tarfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO, cast
from urllib.request import Request, urlopen

PACKS = "https://github.com/wiedehopf/adsb-scripts/releases/download/osm2022/"
PACK_MAX_ZOOM = 10
UA = "multi-conspicuity-provider/offline_tiles"
BBox = tuple[float, float, float, float]  # west, south, east, north
Want = Callable[[int, int, int], bool]  # (z, x, y) -> keep this tile?


def tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    """Web-Mercator tile (x, y) containing lon/lat at zoom z."""
    n = 2**z
    lat = max(min(lat, 85.0511), -85.0511)
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def box(bbox: BBox, z: int) -> tuple[int, int, int, int]:
    """Inclusive tile ranges (x0, x1, y0, y1) covering bbox=(west, south, east, north)."""
    # ponytail: no antimeridian wrap - split a box that crosses 180° into two runs.
    w, s, e, n = bbox
    x0, y0 = tile(w, n, z)
    x1, y1 = tile(e, s, z)
    return x0, x1, y0, y1


def keeper(bbox: BBox, global_zoom: int, bbox_zoom: int) -> Want:
    """The whole world up to global_zoom, plus bbox up to bbox_zoom."""

    def want(z: int, x: int, y: int) -> bool:
        if z <= global_zoom:
            return True
        if z > bbox_zoom:
            return False
        x0, x1, y0, y1 = box(bbox, z)
        return x0 <= x <= x1 and y0 <= y <= y1

    return want


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    part.replace(path)


def unpack(pack: str, out: Path, want: Want) -> None:
    """Stream one tile pack, writing the tiles want(z, x, y) accepts."""
    kept = 0
    req = Request(PACKS + pack, headers={"User-Agent": UA})
    with urlopen(req, timeout=60) as r, tarfile.open(fileobj=r, mode="r|gz") as tar:
        for m in tar:
            # Paths are rebuilt from parsed ints, so a hostile member name can't escape `out`.
            match m.name.strip("./").split("/"):
                case [z_, x_, png] if m.isfile() and png.endswith(".png"):
                    try:
                        z, x, y = int(z_), int(x_), int(png[:-4])
                    except ValueError:
                        continue
                case _:
                    continue
            if want(z, x, y):
                data = cast("IO[bytes]", tar.extractfile(m)).read()  # a file member, checked above
                write(out / str(z) / str(x) / f"{y}.png", data)
                kept += 1
    print(f"{pack}: kept {kept} tiles", flush=True)


def fetch(url: str, out: Path, bbox: BBox, zooms: range, delay: float) -> None:
    for z in zooms:
        x0, x1, y0, y1 = box(bbox, z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                path = out / str(z) / str(x) / f"{y}.png"
                if path.exists():
                    continue
                with urlopen(Request(url.format(z=z, x=x, y=y), headers={"User-Agent": UA}), timeout=30) as r:
                    write(path, r.read())
                time.sleep(delay)
        print(f"zoom {z}: done", flush=True)


def main(argv: Sequence[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bbox", required=True, help="west,south,east,north in degrees")
    p.add_argument(
        "--dir", type=Path, default=os.environ.get("OFFLINE_TILES_DIR", "/usr/local/share/osm_tiles_offline")
    )
    p.add_argument("--global-zoom", type=int, default=6, help="whole-world basemap up to this zoom (default 6)")
    p.add_argument("--bbox-zoom", type=int, default=10, help="detail inside the box up to this zoom (default 10)")
    p.add_argument("--tile-url", help="https://.../{z}/{x}/{y}.png source for zooms above 10")
    p.add_argument("--delay", type=float, default=0.1, help="seconds between --tile-url requests")
    p.add_argument("--max-tiles", type=int, default=50000, help="refuse --tile-url runs bigger than this")
    a = p.parse_args(argv)

    bad_bbox = "--bbox must be west,south,east,north with west<east and south<north"
    try:
        w, s, e, n = (float(v) for v in a.bbox.split(","))
    except ValueError:
        p.error(bad_bbox)
    if not (w < e and s < n):
        p.error(bad_bbox)
    bbox = (w, s, e, n)
    if a.global_zoom > PACK_MAX_ZOOM:
        p.error(f"--global-zoom can be at most {PACK_MAX_ZOOM}")
    deep = range(PACK_MAX_ZOOM + 1, a.bbox_zoom + 1)
    if deep and not a.tile_url:
        p.error(f"--bbox-zoom above {PACK_MAX_ZOOM} needs --tile-url")
    if a.tile_url and not a.tile_url.startswith("https://"):
        p.error("--tile-url must be an https:// URL")
    if a.tile_url and "openstreetmap.org" in a.tile_url:
        p.error("OSM's tile servers forbid bulk/offline downloads - use a provider that allows it")
    count = sum((x1 - x0 + 1) * (y1 - y0 + 1) for x0, x1, y0, y1 in (box(bbox, z) for z in deep))
    if count > a.max_tiles:
        p.error(f"{count} tiles above zoom {PACK_MAX_ZOOM} - shrink the box/zoom or raise --max-tiles")

    os.umask(0o022)  # ultrafeeder's nginx must be able to read them
    want = keeper(bbox, a.global_zoom, a.bbox_zoom)
    unpack("osm_tiles_0_9.tar.gz", a.dir, want)
    if max(a.global_zoom, a.bbox_zoom) >= PACK_MAX_ZOOM:
        unpack("osm_tiles_10.tar.gz", a.dir, want)
    if deep:
        print(f"fetching {count} tiles above zoom {PACK_MAX_ZOOM}", flush=True)
        fetch(a.tile_url, a.dir, bbox, deep, a.delay)
    print(f"done - restart ultrafeeder to pick up {a.dir}")


if __name__ == "__main__":
    main()
