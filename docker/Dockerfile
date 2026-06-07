FROM python:3.11-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY mcp-server ./mcp-server
COPY mcp-client ./mcp-client

RUN uv sync --frozen --no-dev

EXPOSE 8080

CMD ["uv", "run", "mcp-gateway"]
