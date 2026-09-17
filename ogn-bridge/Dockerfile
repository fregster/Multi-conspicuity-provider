# Translates nearby FLARM/OGN traffic into synthetic SBS contacts so tar1090
# shows them on the same map as real ADS-B. Pure Python, so Alpine keeps it small.
FROM python:3.13-alpine
RUN pip install --no-cache-dir ogn-client
COPY bridge.py /bridge.py
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/bridge.py"]
