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
                     |    ADSB Exchange, adsb.one, theairtraffic.com
                     |    (all built into ultrafeeder)
                     |--> piaware      (Beast feed from ultrafeeder)
                     |--> adsbhub      (SBS feed from ultrafeeder)
                     |--> fr24feed     (Beast feed from ultrafeeder)
                     |--> radarbox     (Beast feed from ultrafeeder)
                     |--> planefinder  (Beast feed from ultrafeeder)
                     |--> opensky      (Beast feed from ultrafeeder)

868.8MHz SDR --> ogn-rf --(TCP 50010)--> ogn-decode --> ogn-bridge --> ultrafeeder's SBS input --> tar1090
                                                            |--> aprs.glidernet.org (OGN, when online)
OGN APRS-IS (network-wide, when online) ------------------> ogn-bridge (local SDR wins de-dup)
```

- **ultrafeeder** ([sdr-enthusiasts/docker-adsb-ultrafeeder](https://github.com/sdr-enthusiasts/docker-adsb-ultrafeeder)) decodes 1090MHz ADS-B, serves the tar1090 map, and feeds every network that accepts anonymous Beast/MLAT connections (adsb.lol, adsb.fi, airplanes.live, planespotters.net, ADSB Exchange, adsb.one, theairtraffic.com) directly via its built-in multi-feeder config - none of these need a separate container. Standalone `readsb-protobuf` is archived upstream, so this bundled image is the maintained replacement.
- **piaware** / **adsbhub** / **fr24feed** / **radarbox** / **planefinder** / **opensky** are separate maintained containers for the networks that need their own client software and a per-station key (FlightAware, ADSBHub, FlightRadar24, RadarBox/AirNav Radar, PlaneFinder, OpenSky Network respectively) - they pull Beast/SBS data from `ultrafeeder` over the compose network and have no SDR access themselves.
- **ogn-rf** / **ogn-decode** are custom images (see `ogn/`) built from the official OGN binaries, split the same way: `ogn-rf` owns the 868.8MHz SDR, `ogn-decode` receives its output over TCP and feeds the Open Glider Network APRS server. `ogn-decode` is closed-source freeware (binary-only from glidernet.org); `ogn-rf` is open source but we use the official precompiled binary rather than rebuilding it.
- **ogn-bridge** (see `ogn-bridge/`) gives tar1090 a single unified map with FLARM/OGN traffic alongside ADS-B, and works with **no internet** (e.g. an airfield with only the local SDR). `ogn-decode` is configured to use `ogn-bridge:14580` as its APRS server; the bridge speaks just enough APRS-IS to accept it, turns each position into a synthetic SBS contact (using the `~`-prefixed non-ICAO-address convention `readsb` already supports) for `ultrafeeder`'s SBS input port, and relays every line on to `aprs.glidernet.org` whenever that is reachable (lines are dropped, not queued, while offline - the OGN network wants live data). When online it also subscribes to OGN's public APRS-IS feed with a range filter around the station, so aircraft our dongle can't hear still appear. **De-dup:** the local SDR is treated as the more recent/reliable source - once an aircraft has been heard locally, network reports for the same address are ignored for 30s (`LOCAL_HOLD_S`), so the icon never flips between the two. Trade-off: `ogn-bridge` sits in the path of the OGN upload, so if it is down `ogn-decode` cannot reach the network (it reconnects when the bridge returns). Check with `python3 ogn-bridge/test_bridge.py`.
- **traefik** is a reverse proxy for external HTTPS access, e.g. via a router port-forward to `80`/`443`. It's configured with no certificate resolver, so it falls back to its own auto-generated self-signed certificate - browsers will show a one-time trust warning, but no domain or ACME/DNS setup is required. Every service with a web UI is exposed under a path prefix with its own Traefik `stripprefix` middleware:
  - `/tar1090` - the map (ultrafeeder)
  - `/piaware` - PiAware status page / skyaware
  - `/fr24feed` - FlightRadar24 feed status
  - `/planefinder` - PlaneFinder feed status
  - `/ogn-rf`, `/ogn-decode` - FLARM/OGN decoder status pages

  The landing page also shows live health, refreshed every 15s from `/status.json` (served by the **status** container, see `status/status.py`): every container's state and Docker healthcheck; each ultrafeeder aggregator's Beast connection and MLAT sync (from ultrafeeder's `/metrics` and `/mlat-client-stats/`); the dedicated feeders' health (their own healthchecks check their uplink); and the OGN links reported by `ogn-bridge` (local receiver, local OGN → tar1090, OGN network → tar1090, upload to glidernet). `status` reads the container list through the same read-only `socket-proxy` as Traefik and passes on only name/state/status.

  This works because all of these apps reference their own assets with relative paths - verify that's true before adding another one this way (or use `Host()` rules instead if an app uses root-absolute asset paths). ADSBHub and RadarBox/AirNav Radar have no web UI, so they aren't in the list. The site root (`/`) serves a static holding page (`landing/`, plain nginx) linking to each of these - it's a Traefik catch-all with an explicit low priority so it never shadows the more specific per-service routers. tar1090 also stays reachable directly over plain HTTP on `TAR1090_PORT` (e.g. for LAN use without the cert warning), same as `ogn-rf`/`ogn-decode` on their own `OGN_RF_STATUS_PORT`/`OGN_DECODE_STATUS_PORT`. Note Traefik's Docker provider won't route to a container until its own healthcheck passes, so a freshly (re)started service can 404 through the proxy for a minute or two even though it's actually coming up fine.

## Prerequisites

- One RTL-SDR per frequency you want to receive (1090MHz for ADS-B, 868.8MHz for FLARM/OGN). A single dongle can run ADS-B-only until a second is added.
- Docker + Docker Compose on the host (a Raspberry Pi 5 running Debian is the reference target).

## Setup

1. `cp .env.example .env` and fill in your station's coordinates and each network's API key/UUID.
2. Running locally on the host itself: `docker compose up -d --build`.
3. Deploying to a remote host over SSH (e.g. the Pi): also fill in `PI_HOST`/`PI_USER` in `.env` (used only by `deploy.sh`, not read by Compose itself), then run `./deploy.sh` - it rsyncs the repo over and runs `docker compose up -d --build` there.
4. Starting on boot: on the host running the stack, run `./systemd/install.sh` once. It installs and enables a `multi-conspicuity-provider.service` unit, manageable the normal way: `sudo systemctl {start,stop,restart,status} multi-conspicuity-provider`.

## Host hardening (optional)

`./harden-host.sh` clones [dockerHosting](https://github.com/DigitalisCloudServices/dockerHosting) to `/opt/dockerHosting` and runs its `setup.sh` baseline (ufw, SSH/fail2ban, kernel sysctls, chrony, unattended-upgrades, auditd, AppArmor, Docker daemon log limits). Run it on the host itself - it's interactive and disables SSH password login, so confirm key access first. It is tuned for this stack:

- Skips dockerHosting's own Traefik (this stack runs one) and AIDE (heavy SD-card wear) - override with `HARDEN_SKIP=traefik` on SSD/NVMe.
- Skips USB-storage blacklisting automatically when `/` is on a USB drive.
- Passes `--userns-remap=no`: remapped container root can't open `/dev/bus/usb`, so the SDRs would disappear.

Pin a dockerHosting branch or tag with `DOCKERHOSTING_REF`. Extra arguments go straight to `setup.sh`, e.g. `./harden-host.sh --report` for a read-only posture audit.

## Adding a second SDR

Find each dongle's serial with `rtl_eeprom -d 0` (repeat with `-d 1`, etc.) - stop whichever service currently holds a dongle first (`usb_claim_interface error` means something already has it open). Factory dongles usually all share the same default serial (`00000001`), so set a unique one per dongle with `rtl_eeprom -d <index> -s <serial>` if needed - use a non-numeric serial (e.g. `SDR1090`), since readsb treats a purely-numeric device string as an index rather than a serial. Then set `SDR_1090_SERIAL` and `SDR_FLARM_SERIAL` in `.env` so each service always binds the same physical dongle regardless of USB enumeration order.

## Adding another aggregator network

- If it accepts generic Beast/MLAT feeds (like airplanes.live, ADSB.fi, ADSB Exchange), add a line to `ULTRAFEEDER_CONFIG` in `docker-compose.yml` - see the [Ultrafeeder README](https://github.com/sdr-enthusiasts/docker-adsb-ultrafeeder#all-in-one-configuration-using-ultrafeeder_config) for the aggregator list and connection strings.
- If it needs its own client software (like PiAware/ADSBHub), add a new service pointed at `ultrafeeder`'s Beast (`30005`) or SBS (`30003`) port, following the `piaware`/`adsbhub` services as a template.
