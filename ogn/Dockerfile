# Shared build for both FLARM/OGN containers - which binary this image runs
# is set by the BINARY build arg (see docker-compose.yml).
#
# ogn-rf: reads a 868.8MHz RTL-SDR, streams demodulated spectra to ogn-decode
#   over TCP port 50010. Open source (glidernet/ogn-rf), but we use the
#   official precompiled arm64 binary rather than rebuilding from source.
# ogn-decode: reads demodulated spectra from ogn-rf over TCP and pushes
#   decoded FLARM/OGN positions to the Open Glider Network APRS server.
#   Closed-source freeware, distributed only as a precompiled binary.
ARG BINARY=ogn-rf

FROM debian:trixie-slim AS fetch
ARG BINARY
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN apt-get update && apt-get install -y --no-install-recommends wget ca-certificates && \
    rm -rf /var/lib/apt/lists/*
RUN wget --no-check-certificate -qO- http://download.glidernet.org/arm64/rtlsdr-ogn-bin-arm64-latest.tgz | tar xz -C /tmp && \
    mkdir -p /out && cp /tmp/rtlsdr-ogn/${BINARY} /out/${BINARY}
# EGM96 geoid table: ogn-decode downloads this on every start (it lives on a
# tmpfs) and without it every altitude is ~50m off, so bake it in for offline
# sites - the file is global and never changes.
RUN wget --no-check-certificate -qO /out/WW15MGH.DAC http://download.glidernet.org/common/WW15MGH.DAC && \
    [ "$(stat -c %s /out/WW15MGH.DAC)" -gt 1000000 ]

FROM debian:trixie-slim
ARG BINARY
ENV BINARY=${BINARY}
# librtlsdr0 is only needed by ogn-rf (it owns the SDR) - ogn-decode just
# reads a TCP stream from ogn-rf and has no hardware access. Unquoted on
# purpose so it expands to nothing for ogn-decode.
# hadolint ignore=SC2046
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpng16-16 gettext-base \
      $([ "$BINARY" = "ogn-rf" ] && echo librtlsdr0) && \
    rm -rf /var/lib/apt/lists/*
COPY --from=fetch /out/${BINARY} /usr/local/bin/${BINARY}
# Template lives outside /ogn: /ogn is the (tmpfs) working dir where the
# rendered config and ogn-decode's downloaded geoid file get written.
COPY rtlsdr-ogn.conf.template /usr/local/share/ogn/rtlsdr-ogn.conf.template
COPY --from=fetch /out/WW15MGH.DAC /usr/local/share/ogn/WW15MGH.DAC
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
WORKDIR /ogn
ENTRYPOINT ["/entrypoint.sh"]
