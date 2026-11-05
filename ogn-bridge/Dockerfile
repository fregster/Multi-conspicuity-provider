# Translates nearby FLARM/OGN traffic into synthetic SBS contacts so tar1090
# shows them on the same map as real ADS-B. Pure Python, so Alpine keeps it small.
FROM python:3.15-alpine@sha256:20a82dbc2783112ddb91d1534603f802d222a3e63e593cd002eb2ac534e70211
RUN pip install --no-cache-dir ogn-client==2.0.0
COPY bridge.py /bridge.py
ENV PYTHONUNBUFFERED=1
USER 65534:65534
ENTRYPOINT ["python3", "/bridge.py"]
