FROM python:3.12-slim AS base
ARG PIP_DEFAULT_TIMEOUT=120
ARG PIP_RETRIES=5
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -m pip install . && useradd --uid 10001 --create-home app
USER app
EXPOSE 8000
CMD ["uvicorn", "alves_ia.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS test
USER root
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -m pip install '.[dev]'
COPY tests ./tests
USER app
CMD ["pytest", "-p", "no:cacheprovider", "-v"]

FROM base AS runtime
