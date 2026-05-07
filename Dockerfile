FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/
RUN uv pip install --system --no-cache .

ENTRYPOINT ["ocis-dump"]
