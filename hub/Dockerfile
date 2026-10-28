# UUID/port gate in front of the hub's public ports. Stdlib only.
FROM python:3.13-alpine
COPY gate.py /gate.py
ENV PYTHONUNBUFFERED=1
USER 65534:65534
ENTRYPOINT ["python3", "/gate.py"]
