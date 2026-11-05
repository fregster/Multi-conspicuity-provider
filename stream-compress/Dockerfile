# Per-client gzip/zstd relay in front of the stream ports. Stdlib only: 3.14 is
# the first Python with zstd built in (compression.zstd).
FROM python:3.15-alpine@sha256:20a82dbc2783112ddb91d1534603f802d222a3e63e593cd002eb2ac534e70211
COPY compress.py /compress.py
ENV PYTHONUNBUFFERED=1
USER 65534:65534
ENTRYPOINT ["python3", "/compress.py"]
