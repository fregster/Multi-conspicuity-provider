"""Run: python3 test_offline_tiles.py"""

from offline_tiles import box, keep, tile

assert tile(-180, 85.06, 3) == (0, 0)
assert tile(179.99, -85.06, 3) == (7, 7)
assert tile(0.001, 0.001, 1) == (1, 0)
# London at zoom 10 is tile 511/340 (standard slippy-map numbering).
assert tile(-0.1278, 51.5074, 10) == (511, 340)

bbox = (-2.0, 51.0, 0.0, 52.0)
x0, x1, y0, y1 = box(bbox, 10)
assert x0 < x1 and y0 < y1
assert keep(3, 0, 0, bbox, 6, 10)  # global basemap: anywhere
assert keep(10, 511, 340, bbox, 6, 10)  # inside the box
assert not keep(10, 0, 0, bbox, 6, 10)  # outside the box
assert not keep(11, 1022, 680, bbox, 6, 10)  # beyond --bbox-zoom
print("ok")
