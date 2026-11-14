"""
One OGN beacon, as ogn-client's parse() returns it, as an ASTERIX CAT021
(ADS-B target report) record for readsb's ASTERIX input. Not SBS: readsb
stores an SBS altitude as barometric (field 12 in decodeSbsLine, net_io.c),
but OGN's A= is GNSS height, so FLARM/OGN contacts showed GNSS height as
pressure altitude. readsb's CAT021 reader (decodeAsterixMessage, net_io.c)
puts I021/140 in alt_geom instead. Items, in the order readsb reads them:

  I021/040  target report descriptor, ICAO addresses only: ATP 0 (24-bit ICAO
            address), so readsb merges the contact with the aircraft's own
            Mode S entry. Without it readsb marks the address non-ICAO ("~"),
            which every other OGN address type is (0 unknown, 2 FLARM, 3 OGN):
            those are not ICAO-assigned and may collide with a real one.
            readsb also reads this item as "airborne" (no ground bit is sent).
  I021/131  position, high resolution
  I021/080  target address
  I021/073  time of reception of the position: now, as the SBS lines had
  I021/140  geometric height. A= is height above the EGM96 geoid (OGN's
            Publishing_to_OGN.md; ogn-decode subtracts the geoid height using
            the same WW15MGH.DAC grid), while readsb's alt_geom is height above
            the WGS84 ellipsoid (readsb's README-json.md), so it is added back.
  I021/210  MOPS version: version not supported, link technology 7 (not
            assigned), which readsb reads as address type "unknown". Without
            it readsb keeps its default, adsb_icao, and lists the contact as
            real ADS-B.
  I021/157  geometric vertical rate
  I021/160  ground speed and track, only together
  I021/170  callsign, the beacon's APRS name cut to 8 letters/digits
  I021/020  emitter category, from the OGN aircraft type

readsb gives this input the lowest data priority (SOURCE_INDIRECT), so a real
1090MHz report for the same ICAO address wins until it is 15s stale. There is
no barometric altitude: OGN beacons carry none, and none is made up.
"""

import struct
from datetime import datetime
from pathlib import Path
from typing import Any

ADDRESS_TYPE_ICAO = 1  # OGN address types: 0 unknown, 1 ICAO, 2 FLARM, 3 OGN tracker
FEET_PER_METER = 1 / 0.3048
FPM_PER_MS = 1 / 0.00508
NM_PER_S_PER_KMH = 1 / 1.852 / 3600
# NGA's EGM96 15' grid: 721 rows from 90N to 90S of 1440 columns from 0E
# eastwards, each the geoid height above the ellipsoid in cm (big-endian int16).
GEOID = Path("/WW15MGH.DAC").read_bytes()

# OGN aircraft type -> CAT021 emitter category, picked for the ADS-B category
# readsb's I021/020 table turns it into (decodeAsterixMessage, net_io.c). Types
# with no ADS-B counterpart (0 reserved, A unknown, E ground support) get no
# category, so a real ADS-B one is never overwritten with a guess.
EMITTER_CATEGORY = {
    0x1: 11,  # glider / motor glider -> B1 glider/sailplane
    0x2: 1,  # tow plane -> A1 light
    0x3: 10,  # helicopter / gyrocopter -> A7 rotorcraft
    0x4: 16,  # skydiver -> B3 parachutist/skydiver
    0x5: 1,  # drop plane -> A1 light
    0x6: 15,  # hang glider -> B4 ultralight/hang glider/paraglider
    0x7: 15,  # paraglider -> B4
    0x8: 1,  # piston aircraft -> A1 light
    0x9: 2,  # jet / turboprop -> A2 small: OGN gives no weight class, so the lightest jet one
    0xB: 12,  # balloon -> B2 lighter than air
    0xC: 12,  # airship -> B2
    0xD: 13,  # UAV -> B6 unmanned aerial vehicle
    0xF: 21,  # static obstacle -> C3 point obstacle (readsb maps 21 to C3)
}


def geoid_m(lat: float, lon: float) -> float:
    # ponytail: nearest grid point, within 0.3m in the UK (a few metres where the
    # geoid is steepest) against I021/140's 1.9m step; interpolate if that matters.
    row, col = round((90 - lat) * 4), round(lon % 360 * 4) % 1440
    height_cm: int = struct.unpack_from(">h", GEOID, (row * 1440 + col) * 2)[0]
    return height_cm / 100


def scaled(value: float, lsb: float, bits: int) -> int:
    """value in units of lsb, clamped to a signed field of that many bits. readsb
    drops a clamped height (over 150000ft); the rates only clamp absurd values."""
    top = (1 << (bits - 1)) - 1
    return max(-top - 1, min(top, round(value / lsb)))


def sanitize_callsign(name: str) -> str:
    cleaned = "".join(ch for ch in name.upper() if ch.isascii() and ch.isalnum())[:8]
    return cleaned or "OGN"


def callsign_6bit(name: str) -> bytes:
    """I021/170: 8 characters of 6 bits each; A-Z, 0-9 and space are their ASCII codes' low 6 bits."""
    bits = 0
    for ch in sanitize_callsign(name).ljust(8):
        bits = (bits << 6) | (ord(ch) & 0x3F)
    return bits.to_bytes(6, "big")


def data_block(items: dict[int, bytes]) -> bytes:
    """A CAT021 data block of one record, items keyed by field reference number."""
    fspec = bytearray((max(items) + 6) // 7)
    for frn in items:
        fspec[(frn - 1) // 7] |= 0x80 >> ((frn - 1) % 7)
    for i in range(len(fspec) - 1):
        fspec[i] |= 1  # FX: another FSPEC octet follows
    body = bytes(fspec) + b"".join(items[frn] for frn in sorted(items))
    return struct.pack(">BH", 21, 3 + len(body)) + body


def motion(beacon: dict[str, Any], items: dict[int, bytes]) -> None:
    """The optional items: height, climb rate, ground vector, category."""
    if "altitude" in beacon:  # I021/140, 6.25ft
        hae_m = beacon["altitude"] + geoid_m(beacon["latitude"], beacon["longitude"])
        items[16] = struct.pack(">h", scaled(hae_m * FEET_PER_METER, 6.25, 16))
    if "climb_rate" in beacon:  # I021/157, 15 bits of 6.25ft/min
        items[25] = struct.pack(">H", scaled(beacon["climb_rate"] * FPM_PER_MS, 6.25, 15) & 0x7FFF)
    if "ground_speed" in beacon and "track" in beacon:  # I021/160, 2^-14NM/s and 360/2^16 deg
        speed = scaled(beacon["ground_speed"] * NM_PER_S_PER_KMH, 2**-14, 16)  # negative: range-exceeded bit
        items[26] = struct.pack(">hH", speed, round(beacon["track"] % 360 * 65536 / 360) % 65536)
    if (category := EMITTER_CATEGORY.get(beacon.get("aircraft_type", 0))) is not None:  # I021/020
        items[30] = bytes([category])


def to_cat021(beacon: dict[str, Any], now: datetime) -> bytes | None:
    """Aircraft position reports only - not receiver or weather-station beacons."""
    if beacon.get("aprs_type") != "position":
        return None
    if not all(beacon.get(k) is not None for k in ("address", "latitude", "longitude")):
        return None
    lat, lon = beacon["latitude"], beacon["longitude"]
    since_midnight_s = now.hour * 3600 + now.minute * 60 + now.second + now.microsecond / 1e6
    items = {  # field reference number -> item
        7: struct.pack(">ii", round(lat * 2**30 / 180), round(lon * 2**30 / 180)),  # I021/131
        11: bytes.fromhex(beacon["address"]),  # I021/080
        12: int(since_midnight_s * 128).to_bytes(3, "big"),  # I021/073, 1/128s
        18: bytes([0x40 | 7]),  # I021/210: VNS, LTT 7
        29: callsign_6bit(beacon.get("name", "OGN")),  # I021/170
    }
    if beacon.get("address_type") == ADDRESS_TYPE_ICAO:
        items[2] = bytes([0x10])  # I021/040 ATP 0: 24-bit ICAO address; ARC 2: no barometric altitude
    motion(beacon, items)
    return data_block(items)
