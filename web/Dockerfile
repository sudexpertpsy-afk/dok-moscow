# Контекст сборки — корень репозитория (см. deploy/compose.yml и CI).
FROM python:3.12-slim

ARG APP_VERSION=dev
ARG GIT_REVISION=unknown

LABEL org.opencontainers.image.revision=${GIT_REVISION} \
      org.opencontainers.image.version=${APP_VERSION} \
      org.opencontainers.image.title="dok-app" \
      org.opencontainers.image.description="Док.Москва"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/core \
    FILES_ROOT=/srv/dok/data/files \
    APP_VERSION=${APP_VERSION} \
    GIT_REVISION=${GIT_REVISION}

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
      fonts-liberation \
      fonts-dejavu-core \
      fontconfig \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

COPY web/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY web/app ./app
COPY web/alembic.ini ./alembic.ini
COPY web/alembic ./alembic
COPY web/scripts ./scripts
COPY web/manage.py ./manage.py
COPY web/docker-entrypoint.sh /docker-entrypoint.sh
# W-46: ядро и seed-шаблоны в образе (без bind ../core на проде)
COPY core ./core

RUN chmod +x /docker-entrypoint.sh \
    && mkdir -p /srv/dok/data/files \
    && python scripts/hash_static.py

EXPOSE 8000
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
