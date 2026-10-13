FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    COLUMNS=120

WORKDIR /srv

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd -m -u 10001 appuser \
    && mkdir -p /srv/data/reports \
    && chown -R appuser:appuser /srv
USER appuser

# Celery worker; concurrency configurable via env
CMD ["sh", "-c", "celery -A app.workers.celery_app.celery_app worker --loglevel=INFO --concurrency=${CELERY_CONCURRENCY:-2}"]
