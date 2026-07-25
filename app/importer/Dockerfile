FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp

WORKDIR /app

COPY app/importer/requirements.txt /tmp/requirements.txt

RUN python -m pip install \
    --no-cache-dir \
    --requirement /tmp/requirements.txt

COPY scripts /app/scripts

ENTRYPOINT ["python", "/app/scripts/import-workbook.py"]
