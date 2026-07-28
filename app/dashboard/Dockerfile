FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp

WORKDIR /app

RUN useradd \
    --create-home \
    --uid 10001 \
    --shell /usr/sbin/nologin \
    appuser

COPY requirements.txt /tmp/requirements.txt

RUN python -m pip install \
    --no-cache-dir \
    --requirement /tmp/requirements.txt

COPY app.py /app/app.py
COPY .streamlit /app/.streamlit

USER 10001:10001

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
