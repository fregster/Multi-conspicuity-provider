# Per-client gzip/zstd relay in front of the stream ports. Stdlib only: 3.14 is
# the first Python with zstd built in (compression.zstd).
FROM python:3.14-alpine@sha256:bc62fbcb684904b1bb81a6240041bb60e9c1ee19ddeaf17a6b4297e1cc9c95d2
COPY compress.py /compress.py
ENV PYTHONUNBUFFERED=1
USER 65534:65534
ENTRYPOINT ["python3", "/compress.py"]
