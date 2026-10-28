"""Run: python3 test_offline_tiles.py"""

import contextlib
import io
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest import mock

import offline_tiles
from offline_tiles import box, keeper, tile

assert tile(-180, 85.06, 3) == (0, 0)
assert tile(179.99, -85.06, 3) == (7, 7)
assert tile(0.001, 0.001, 1) == (1, 0)
# London at zoom 10 is tile 511/340 (standard slippy-map numbering).
assert tile(-0.1278, 51.5074, 10) == (511, 340)

bbox = (-2.0, 51.0, 0.0, 52.0)
x0, x1, y0, y1 = box(bbox, 10)
assert x0 < x1 and y0 < y1
want = keeper(bbox, 6, 10)
assert want(3, 0, 0)  # global basemap: anywhere
assert want(10, 511, 340)  # inside the box
assert not want(10, 0, 0)  # outside the box
assert not want(11, 1022, 680)  # beyond --bbox-zoom


def pack(members: dict[str, bytes | None]) -> bytes:
    """A .tar.gz of name -> file bytes (None = a directory)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# unpack(): keeps wanted z/x/y.png files; anything else in the pack is skipped, and a
# hostile name can't write outside the output directory.
TAR = pack(
    {
        "./3/1": None,  # directory
        "./3/1/2.png": b"kept",
        "./3/1/5.png": b"unwanted",
        "./3/x/2.png": b"not a number",
        "./3/1/2.txt": b"not a png",
        "../../evil/9.png": b"escape",
        "3/1/2/3.png": b"too deep",
    }
)
urls: list[Any] = []


def fake_urlopen(req: Any, timeout: float) -> io.BytesIO:
    urls.append(req)
    return io.BytesIO(TAR)


with tempfile.TemporaryDirectory() as d, mock.patch.object(offline_tiles, "urlopen", fake_urlopen):
    out = Path(d) / "tiles"
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        offline_tiles.unpack("p.tar.gz", out, lambda z, x, y: (z, x, y) != (3, 1, 5))
    assert urls[0].full_url == offline_tiles.PACKS + "p.tar.gz"
    assert sorted(str(p.relative_to(d)) for p in Path(d).rglob("*") if p.is_file()) == ["tiles/3/1/2.png"]
    assert (out / "3/1/2.png").read_bytes() == b"kept"
    assert printed.getvalue() == "p.tar.gz: kept 1 tiles\n"


# fetch(): every tile in the box at each zoom, skipping ones already on disk, paced by delay.
def fake_tile(req: Any, timeout: float) -> io.BytesIO:
    urls.append(req.full_url)
    return io.BytesIO(req.full_url.encode())


small = (-0.13, 51.50, -0.12, 51.51)
with (
    tempfile.TemporaryDirectory() as d,
    mock.patch.object(offline_tiles, "urlopen", fake_tile),
    mock.patch.object(time, "sleep") as sleep,
    contextlib.redirect_stdout(io.StringIO()),
):
    out = Path(d)
    tx0, tx1, ty0, ty1 = box(small, 12)
    have = out / "12" / str(tx0) / f"{ty0}.png"
    have.parent.mkdir(parents=True)
    have.write_bytes(b"old")
    urls.clear()
    offline_tiles.fetch("https://t/{z}/{x}/{y}.png", out, small, range(12, 13), 0.5)
    n = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
    assert len(urls) == n - 1  # the existing tile isn't refetched
    assert have.read_bytes() == b"old"
    url = f"https://t/12/{tx1}/{ty1}.png"
    assert (out / "12" / str(tx1) / f"{ty1}.png").read_bytes() == url.encode()
    assert sleep.call_count == n - 1
    sleep.assert_called_with(0.5)


# main(): argument errors exit 2 before anything is downloaded.
def run(*args: str) -> tuple[int | str | None, str, list[Any], list[Any]]:
    err = io.StringIO()
    code: int | str | None = 0
    with (
        mock.patch.object(offline_tiles, "unpack") as unpack,
        mock.patch.object(offline_tiles, "fetch") as fetch,
        contextlib.redirect_stderr(err),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        try:
            offline_tiles.main(["--dir", "/x", *args])
        except SystemExit as e:
            code = e.code
    return code, err.getvalue(), unpack.call_args_list, fetch.call_args_list


B = "--bbox=-2,51,0,52"
for args, msg in [
    (["--bbox", "a,b,c,d"], "--bbox must be"),
    (["--bbox", "1,2,3"], "--bbox must be"),
    (["--bbox", "0,51,-2,52"], "--bbox must be"),  # west > east
    (["--bbox=-2,52,0,51"], "--bbox must be"),  # south > north
    ([B, "--global-zoom", "11"], "--global-zoom can be at most 10"),
    ([B, "--bbox-zoom", "11"], "needs --tile-url"),
    ([B, "--bbox-zoom", "11", "--tile-url", "http://t/{z}/{x}/{y}.png"], "must be an https:// URL"),
    ([B, "--bbox-zoom", "11", "--tile-url", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"], "forbid bulk"),
    ([B, "--bbox-zoom", "14", "--tile-url", "https://t/{z}/{x}/{y}.png", "--max-tiles", "10"], "raise --max-tiles"),
]:
    code, err, unpacked, fetched = run(*args)
    assert code == 2 and msg in err and not unpacked and not fetched, (args, err)

# Up to zoom 10: both packs, nothing fetched.
code, _, unpacked, fetched = run(B)
assert code == 0 and [c.args[0] for c in unpacked] == ["osm_tiles_0_9.tar.gz", "osm_tiles_10.tar.gz"]
assert unpacked[0].args[1] == Path("/x") and not fetched
want = unpacked[0].args[2]
assert want(10, 511, 340) and not want(10, 0, 0)  # the box's own keeper
# Below zoom 10: the zoom-10 pack is skipped.
code, _, unpacked, fetched = run(B, "--global-zoom", "4", "--bbox-zoom", "8")
assert code == 0 and [c.args[0] for c in unpacked] == ["osm_tiles_0_9.tar.gz"] and not fetched
# Deeper: packs, then the rest from --tile-url.
code, _, unpacked, fetched = run(B, "--bbox-zoom", "11", "--tile-url", "https://t/{z}/{x}/{y}.png", "--delay", "0")
assert code == 0 and len(unpacked) == 2
assert fetched[0].args == ("https://t/{z}/{x}/{y}.png", Path("/x"), (-2.0, 51.0, 0.0, 52.0), range(11, 12), 0.0)
print("ok")
