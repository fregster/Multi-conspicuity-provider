# Multi-conspicuity Provider

Docker Compose stack for feeding one Raspberry Pi with multiple RTL-SDR dongles
into several ADS-B and FLARM/OGN aggregator networks, plus a local unified map.

## Architecture

Each frequency gets a single decoder container that owns its SDR and re-publishes
the decoded traffic over the compose network. Every aggregator network then runs
as its own lightweight container that consumes that shared feed - no aggregator's
client software talks to the hardware directly, so dongles are never contended
for and networks can be added/removed independently.

```
 1090MHz SDR --> ultrafeeder (readsb + tar1090 + mlat-hub)
                     |--> local map (tar1090, http://<pi>:8080)
                     |--> adsb.lol, adsb.fi, airplanes.live, planespotters.net,
                     |    ADSB Exchange (all built into ultrafeeder)
                     |--> piaware      (Beast feed from ultrafeeder)
                     |--> adsbhub      (SBS feed from ultrafeeder)
                     |--> fr24feed     (Beast feed from ultrafeeder)
                     |--> radarbox     (Beast feed from ultrafeeder)
                     |--> planefinder  (Beast feed from ultrafeeder)

868.8MHz SDR --> ogn-rf --(TCP 50010)--> ogn-decode --> aprs.glidernet.org (OGN)
```

- **ultrafeeder** ([sdr-enthusiasts/docker-adsb-ultrafeeder](https://github.com/sdr-enthusiasts/docker-adsb-ultrafeeder)) decodes 1090MHz ADS-B, serves the tar1090 map, and feeds every network that accepts anonymous Beast/MLAT connections (adsb.lol, adsb.fi, airplanes.live, planespotters.net, ADSB Exchange) directly via its built-in multi-feeder config - none of these need a separate container. Standalone `readsb-protobuf` is archived upstream, so this bundled image is the maintained replacement.
- **piaware** / **adsbhub** / **fr24feed** / **radarbox** / **planefinder** are separate maintained containers for the networks that need their own client software and a per-station key (FlightAware, ADSBHub, FlightRadar24, RadarBox/AirNav Radar, PlaneFinder respectively) - they pull Beast/SBS data from `ultrafeeder` over the compose network and have no SDR access themselves.
- **ogn-rf** / **ogn-decode** are custom images (see `ogn/`) built from the official OGN binaries, split the same way: `ogn-rf` owns the 868.8MHz SDR, `ogn-decode` receives its output over TCP and feeds the Open Glider Network APRS server. `ogn-decode` is closed-source freeware (binary-only from glidernet.org); `ogn-rf` is open source but we use the official precompiled binary rather than rebuilding it.

## Prerequisites

- One RTL-SDR per frequency you want to receive (1090MHz for ADS-B, 868.8MHz for FLARM/OGN). A single dongle can run ADS-B-only until a second is added.
- Docker + Docker Compose on the host (a Raspberry Pi 5 running Debian is the reference target).

## Setup

1. `cp .env.example .env` and fill in your station's coordinates and each network's API key/UUID.
2. Running locally on the host itself: `docker compose up -d --build`.
3. Deploying to a remote host over SSH (e.g. the Pi): copy `secrets/pi.env.example` to `secrets/pi.env`, fill in `PI_HOST`/`PI_USER` (git-ignored, not read by Compose itself), then run `./deploy.sh` - it rsyncs the repo over and runs `docker compose up -d --build` there.

## Adding a second SDR

Find each dongle's serial with `rtl_eeprom -d 0` (repeat with `-d 1`, etc.), set it permanently if needed, then set `SDR_1090_SERIAL` / pin `ogn-rf`'s device in `.env` so each service always binds the same physical dongle regardless of USB enumeration order.

## Adding another aggregator network

- If it accepts generic Beast/MLAT feeds (like airplanes.live, ADSB.fi, ADSB Exchange), add a line to `ULTRAFEEDER_CONFIG` in `docker-compose.yml` - see the [Ultrafeeder README](https://github.com/sdr-enthusiasts/docker-adsb-ultrafeeder#all-in-one-configuration-using-ultrafeeder_config) for the aggregator list and connection strings.
- If it needs its own client software (like PiAware/ADSBHub), add a new service pointed at `ultrafeeder`'s Beast (`30005`) or SBS (`30003`) port, following the `piaware`/`adsbhub` services as a template.
