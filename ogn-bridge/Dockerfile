# Translates nearby FLARM/OGN traffic into ASTERIX CAT021 contacts so tar1090
# shows them on the same map as real ADS-B. Pure Python, so Alpine keeps it small.
FROM python:3.13-alpine@sha256:8bcfa47b04b853a17aaf909362b847b2112fd6ec4105ea553ef9a43bafbb5916
RUN pip install --no-cache-dir ogn-client==2.0.0
SHELL ["/bin/ash", "-o", "pipefail", "-c"]
# EGM96 geoid table, the one ogn-decode takes the geoid height off with (see
# ogn/Dockerfile): asterix.py adds it back, as readsb's alt_geom is above the
# ellipsoid. Global and never changes, so its hash is pinned.
RUN wget -qO /WW15MGH.DAC http://download.glidernet.org/common/WW15MGH.DAC && \
    echo "9dcaf4efd6857aaa2e1e2e2c574d3f5264eacf47024cdcd5291c40ddad5e5acc  /WW15MGH.DAC" | sha256sum -c -
COPY asterix.py bridge.py /
ENV PYTHONUNBUFFERED=1
USER 65534:65534
ENTRYPOINT ["python3", "/bridge.py"]
