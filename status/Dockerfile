# Serves /status.json for the landing page. Stdlib only.
FROM python:3.13-alpine
COPY status.py /status.py
ENV PYTHONUNBUFFERED=1
USER 65534:65534
ENTRYPOINT ["python3", "/status.py"]
