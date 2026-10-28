# Translates nearby FLARM/OGN traffic into synthetic SBS contacts so tar1090
# shows them on the same map as real ADS-B. Pure Python, so Alpine keeps it small.
FROM python:3.13-alpine@sha256:8bcfa47b04b853a17aaf909362b847b2112fd6ec4105ea553ef9a43bafbb5916
RUN pip install --no-cache-dir ogn-client==2.0.0
COPY bridge.py /bridge.py
ENV PYTHONUNBUFFERED=1
USER 65534:65534
ENTRYPOINT ["python3", "/bridge.py"]
