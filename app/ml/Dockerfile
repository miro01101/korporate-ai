FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    OMP_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2 \
    MKL_NUM_THREADS=2 \
    NUMEXPR_NUM_THREADS=2

WORKDIR /app

RUN apt-get update \
    && apt-get install \
        --yes \
        --no-install-recommends \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd \
    --create-home \
    --uid 10002 \
    --shell /usr/sbin/nologin \
    mluser

COPY app/ml/requirements.txt /tmp/requirements.txt

RUN python -m pip install \
    --no-cache-dir \
    --requirement /tmp/requirements.txt

COPY app/ml/ml_pipeline /app/ml_pipeline
COPY app/ml/tests /app/tests

USER 10002:10002

ENTRYPOINT ["python", "-m", "ml_pipeline.cli"]
CMD ["self-check"]
