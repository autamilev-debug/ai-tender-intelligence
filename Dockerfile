FROM python:3.12-slim-bookworm AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml constraints.txt README.md ./
COPY src/ src/
COPY prompts/ prompts/
COPY reference-data/ reference-data/
COPY migrations/ migrations/
COPY discovery_migrations/ discovery_migrations/
COPY document_migrations/ document_migrations/
COPY extraction_migrations/ extraction_migrations/
COPY alembic.ini ./
RUN python -m pip wheel --wheel-dir /wheels -c constraints.txt . \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-index --find-links=/wheels -c constraints.txt ai-tender-intelligence \
    && /opt/venv/bin/pip check

FROM python:3.12-slim-bookworm AS runtime
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 APP_ENV=local
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app
COPY --from=builder /opt/venv /opt/venv
COPY scripts/check_installed_package.py /opt/checks/check_installed_package.py
WORKDIR /app
USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=10s --timeout=4s --start-period=5s --retries=3 \
    CMD ["python", "-m", "ai_tender_intelligence.container_runtime", "--probe"]
ENTRYPOINT ["python", "-m", "ai_tender_intelligence.container_runtime"]
