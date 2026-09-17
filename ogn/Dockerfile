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
RUN apt-get update && apt-get install -y --no-install-recommends wget ca-certificates && \
    rm -rf /var/lib/apt/lists/*
RUN wget --no-check-certificate -qO- http://download.glidernet.org/arm64/rtlsdr-ogn-bin-arm64-latest.tgz | tar xz -C /tmp && \
    mkdir -p /out && cp /tmp/rtlsdr-ogn/${BINARY} /out/${BINARY}

FROM debian:trixie-slim
ARG BINARY
ENV BINARY=${BINARY}
# librtlsdr0 is only needed by ogn-rf (it owns the SDR) - ogn-decode just
# reads a TCP stream from ogn-rf and has no hardware access.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpng16-16 gettext-base \
      $([ "$BINARY" = "ogn-rf" ] && echo librtlsdr0) && \
    rm -rf /var/lib/apt/lists/*
COPY --from=fetch /out/${BINARY} /usr/local/bin/${BINARY}
COPY rtlsdr-ogn.conf.template /ogn/rtlsdr-ogn.conf.template
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
WORKDIR /ogn
ENTRYPOINT ["/entrypoint.sh"]
