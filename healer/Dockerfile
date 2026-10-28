# Restarts unhealthy autoheal-labelled containers. Stdlib only.
FROM python:3.13-alpine
COPY healer.py /healer.py
ENV PYTHONUNBUFFERED=1
USER 65534:65534
ENTRYPOINT ["python3", "/healer.py"]
