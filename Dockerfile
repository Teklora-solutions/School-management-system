FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

# Native libraries for WeasyPrint, Pillow and the PDF export backends.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 libpango-1.0-0 libpangoft2-1.0-0 libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 libffi8 shared-mime-info fonts-dejavu-core gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements/ ./requirements/
# Mount the installer only for this build step; no extra tool in the runtime.
RUN --mount=from=ghcr.io/astral-sh/uv:0.9.7,source=/uv,target=/bin/uv \
    uv pip install --system --no-cache -r requirements/prod.txt

RUN groupadd --gid 10001 school \
    && useradd --uid 10001 --gid school --no-create-home school
COPY --chown=school:school backend/ ./backend/
COPY --chown=school:school frontend/ ./frontend/
COPY --chmod=755 deploy/entrypoint.sh /usr/local/bin/school-entrypoint

WORKDIR /app/backend
ENV XDG_CACHE_HOME=/tmp/.cache
USER school
ENTRYPOINT ["school-entrypoint"]
CMD ["gunicorn", "config.wsgi:application", "--bind=0.0.0.0:8000", "--workers=2", "--timeout=120", "--access-logfile=-", "--error-logfile=-"]
