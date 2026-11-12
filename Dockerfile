# syntax=docker/dockerfile:1.7
#
# Settlement Portal - production container image.
#
# Build:
#   docker build --network=host -t settlement-api:latest .
# or:
#   docker compose build
#
# The image never contains .env files, Oracle passwords, private keys, logs,
# or runtime data. Provide secrets at runtime through environment variables or
# the *_FILE Docker-secret convention supported by docker/entrypoint.py.

ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------------------------
# Build stage: install Python dependencies into an isolated virtualenv.
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --no-compile -r requirements.txt \
    && find /opt/venv -type d -name __pycache__ -prune -exec rm -rf {} + \
    && find /opt/venv -type f -name '*.pyc' -delete

# ---------------------------------------------------------------------------
# Runtime stage: minimal image, non-root user, no build toolchain.
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

LABEL org.opencontainers.image.title="Settlement Portal" \
      org.opencontainers.image.description="Django settlement portal and API" \
      org.opencontainers.image.version="1.0.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    HOME=/tmp \
    DJANGO_SETTINGS_MODULE=config.settings \
    PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid app \
        --home-dir /app --no-create-home --no-log-init \
        --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=root:root manage.py ./
COPY --chown=root:root config ./config
COPY --chown=root:root core ./core
COPY --chown=root:root apps ./apps
COPY --chown=root:root templates ./templates
COPY --chown=root:root static ./static

# Build static assets at image-build time; the runtime filesystem is
# read-only. Dummy values are only used for settings import during the build.
RUN SECRET_KEY="build-only-secret-not-used-at-runtime-000000000000" \
    DEBUG=0 \
    ALLOWED_HOSTS=localhost \
    PORTAL_ORACLE_DSN=localhost:1521/xe \
    PORTAL_ORACLE_USER=build_user \
    PORTAL_ORACLE_PASSWORD=build_password \
    python manage.py collectstatic --noinput --clear \
    && chown -R root:root /app \
    && chmod -R u=rwX,go=rX /app

COPY --chmod=0755 docker/entrypoint.py /usr/local/bin/entrypoint.py
COPY --chmod=0755 docker/healthcheck.py /usr/local/bin/healthcheck.py

USER app:app

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "/usr/local/bin/healthcheck.py"]

ENTRYPOINT ["python", "/usr/local/bin/entrypoint.py"]
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--threads", "2", \
     "--timeout", "30", \
     "--graceful-timeout", "30", \
     "--keep-alive", "5", \
     "--max-requests", "1000", \
     "--max-requests-jitter", "100", \
     "--worker-tmp-dir", "/tmp", \
     "--limit-request-line", "4094", \
     "--limit-request-fields", "100", \
     "--limit-request-field_size", "8190", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info"]
