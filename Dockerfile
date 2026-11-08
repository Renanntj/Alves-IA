FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home app
USER app
EXPOSE 8000
CMD ["uvicorn", "alves_ia.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS test
USER root
RUN pip install --no-cache-dir '.[dev]'
COPY tests ./tests
USER app
CMD ["pytest", "-p", "no:cacheprovider", "-v"]

FROM base AS runtime
