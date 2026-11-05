# Restarts unhealthy autoheal-labelled containers. Stdlib only.
FROM python:3.15-alpine@sha256:20a82dbc2783112ddb91d1534603f802d222a3e63e593cd002eb2ac534e70211
COPY healer.py /healer.py
ENV PYTHONUNBUFFERED=1
USER 65534:65534
ENTRYPOINT ["python3", "/healer.py"]
