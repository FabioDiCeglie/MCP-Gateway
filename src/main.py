from __future__ import annotations

import sys
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI

from config import load_config
from routes import health_router, mcp_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient(follow_redirects=False) as client:
        app.state.http_client = client
        yield


app = FastAPI(lifespan=lifespan)
app.include_router(health_router)
app.include_router(mcp_router)


def main() -> None:
    try:
        config = load_config()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    app.state.config = config

    print(f"Listening on {config.listen.host}:{config.listen.port} → {config.upstream.url}")
    uvicorn.run(app, host=config.listen.host, port=config.listen.port)


if __name__ == "__main__":
    main()
