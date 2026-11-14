"""Run: python3 test_asterix.py, in the ogn-bridge image (it holds the geoid grid).
Item values follow EUROCONTROL's CAT021 definitions and the scaling readsb's
decodeAsterixMessage() reads them with; test_sbs_leak.sh feeds these records to
the real readsb."""

import struct
from datetime import UTC, datetime

import asterix

# Length of each item this encoder writes, by field reference number.
ITEM_LEN = {2: 1, 7: 8, 11: 3, 12: 3, 16: 2, 18: 1, 25: 2, 26: 4, 29: 6, 30: 1}
AIS = "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_ !\"#$%&'()*+,-./0123456789:;<=>?"  # readsb's ais_charset
NOW = datetime(2026, 11, 14, 0, 0, 1, 500000, tzinfo=UTC)


def items(block: bytes) -> dict[int, bytes]:
    """Splits a one-record CAT021 data block back into its items, as readsb walks it."""
    assert block[0] == 21
    assert struct.unpack(">H", block[1:3])[0] == len(block)
    p, frns = 3, []
    while True:
        octet = block[p]
        frns += [(p - 3) * 7 + bit + 1 for bit in range(7) if octet & (0x80 >> bit)]
        p += 1
        if not octet & 1:
            break
    out = {}
    for frn in frns:
        out[frn] = block[p : p + ITEM_LEN[frn]]
        p += ITEM_LEN[frn]
    assert p == len(block)
    return out


def callsign(item: bytes) -> str:
    bits = int.from_bytes(item, "big")
    return "".join(AIS[(bits >> shift) & 0x3F] for shift in range(42, -1, -6))


BEACON = {
    "aprs_type": "position",
    "address": "4080F6",
    "address_type": asterix.ADDRESS_TYPE_ICAO,
    "aircraft_type": 0x1,
    "name": "g-abcd/x!",
    "latitude": 51.5,
    "longitude": -1.25,
    "altitude": 1000.0,
    "ground_speed": 185.2,
    "track": 90.0,
    "climb_rate": 1.016,
}

# Every item, ICAO address.
full = items(asterix.to_cat021(BEACON, NOW) or b"")
assert sorted(full) == [2, 7, 11, 12, 16, 18, 25, 26, 29, 30]
assert full[2] == b"\x10"  # ATP 0 (24-bit ICAO): readsb keeps the address without "~"
lat, lon = struct.unpack(">ii", full[7])
assert abs(lat * 180 / 2**30 - 51.5) < 1e-7 > abs(lon * 180 / 2**30 + 1.25)  # 180/2^30 deg steps
assert full[11] == bytes.fromhex("4080F6")
assert full[12] == (192).to_bytes(3, "big")  # 1.5s after midnight, in 1/128s
# A= 1000m above the geoid + 47.0m geoid height here = 1047.0m above the ellipsoid.
geoid = asterix.geoid_m(51.5, -1.25)
assert 46 < geoid < 48, geoid
assert struct.unpack(">h", full[16])[0] == round((1000 + geoid) / 0.3048 / 6.25)
assert full[18] == b"\x47"  # VNS, LTT 7: readsb's address type "unknown", never adsb_icao
assert full[25] == struct.pack(">H", 32)  # 1.016 m/s = 200 ft/min = 32 x 6.25
speed, track = struct.unpack(">HH", full[26])
assert (round(speed * 2**-14 * 3600, 1), track) == (100.0, 16384)  # 185.2 km/h = 100 kt; 90 deg
assert callsign(full[29]) == "GABCDX  "
assert full[30] == bytes([11])  # glider: readsb's category B1

# Only what the beacon holds; a non-ICAO address sends no I021/040, so readsb adds the "~".
bare = items(
    asterix.to_cat021({"aprs_type": "position", "address": "DDA5BA", "latitude": 0, "longitude": 0}, NOW) or b""
)
assert sorted(bare) == [7, 11, 12, 18, 29]
assert callsign(bare[29]) == "OGN     "  # no name: OGN
for address_type in (0, 2, 3):  # unknown, FLARM, OGN tracker: all non-ICAO
    assert 2 not in items(asterix.to_cat021({**BEACON, "address_type": address_type}, NOW) or b"")

# The ground vector goes only with both halves; each optional item stands alone.
for key, frn in [("altitude", 16), ("climb_rate", 25), ("ground_speed", 26), ("track", 26), ("aircraft_type", 30)]:
    assert frn not in items(asterix.to_cat021({k: v for k, v in BEACON.items() if k != key}, NOW) or b"")

# Not an aircraft position report: nothing sent.
assert asterix.to_cat021({**BEACON, "aprs_type": "status"}, NOW) is None
for key in ("address", "latitude", "longitude"):
    assert asterix.to_cat021({**BEACON, key: None}, NOW) is None

# OGN aircraft type -> category; no claim where ADS-B has no match.
want_categories = [None, 11, 1, 10, 16, 1, 15, 15, 1, 2, None, 12, 12, 13, None, 21]  # by OGN type 0-F
assert [asterix.EMITTER_CATEGORY.get(t) for t in range(16)] == want_categories
assert 30 not in items(asterix.to_cat021({**BEACON, "aircraft_type": 0xA}, NOW) or b"")

# Out of range: height clamped where readsb drops it, rate clamped, speed range-exceeded, track wrapped.
odd = {**BEACON, "altitude": 304799.7, "climb_rate": -1000.0, "ground_speed": -1.0, "track": 360}
wild = items(asterix.to_cat021(odd, NOW) or b"")
assert struct.unpack(">h", wild[16])[0] * 6.25 > 150000  # readsb keeps -1500..150000ft only
assert wild[25] == struct.pack(">H", 0x4000)  # most negative 15-bit rate
assert wild[26][0] & 0x80  # RE bit: readsb ignores the vector
assert wild[26][2:] == b"\x00\x00"

# Callsign: letters and digits only, 8 at most, OGN when nothing is left.
assert asterix.sanitize_callsign("FLRDDA5BAxx") == "FLRDDA5B"
assert asterix.sanitize_callsign("é!") == "OGN"  # non-ASCII has no 6-bit code

# Geoid grid: textbook EGM96 values, and longitude wraps.
assert (asterix.geoid_m(90, 0), asterix.geoid_m(-90, 0), asterix.geoid_m(0, 0)) == (13.61, -29.53, 17.16)
assert asterix.geoid_m(10, 359.9) == asterix.geoid_m(10, 0) == asterix.geoid_m(10, -360)

# More than 7 items needs an FX bit on every FSPEC octet but the last.
assert asterix.data_block({1: b"", 8: b"", 15: b""})[3:] == b"\x81\x81\x80"
print("ok")
