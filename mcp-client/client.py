"""Smoke-test client: initialize, tools/list, and tools/call through the gateway."""

from __future__ import annotations

import argparse
import os
import sys
import time

import anyio
import httpx
import jwt
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

DEFAULT_GATEWAY_URL = "http://127.0.0.1:8080/mcp"

# Stand-in for a logged-in user in a real app (e.g. React).
# In production the client receives a JWT from a /login route or external IdP —
# it does not sign tokens itself. Auto-signing below is for local smoke tests only.
CLIENT = {"client_identity": "smoke-client"}


def _resolve_bearer_token(token: str | None) -> str | None:
    if token:
        return token

    secret = os.environ.get("GATEWAY_JWT_SECRET", "").strip()
    if not secret:
        return None

    now = int(time.time())
    return jwt.encode(
        {"sub": CLIENT["client_identity"], "iat": now, "exp": now + 3600},
        secret,
        algorithm="HS256",
    )


async def run(url: str, bearer_token: str | None) -> None:
    headers = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    async with httpx.AsyncClient(headers=headers) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (
            read,
            write,
            _,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = [tool.name for tool in tools.tools]
                print(f"Connected to {url}")
                print(f"Tools: {', '.join(names)}")

                try:
                    await session.call_tool("echo", {"message": "hello"})
                    print("echo: hello")
                except Exception as exc:
                    print(f"echo: denied ({exc})")

                try:
                    await session.call_tool("ping", {})
                    print("ping: pong")
                except Exception as exc:
                    print(f"ping: denied ({exc})")


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP gateway smoke-test client")
    parser.add_argument(
        "--url",
        default=DEFAULT_GATEWAY_URL,
        help=f"Gateway MCP endpoint (default: {DEFAULT_GATEWAY_URL})",
    )
    parser.add_argument(
        "--token",
        help="Bearer JWT (overrides auto-signing)",
    )
    args = parser.parse_args()

    bearer_token = _resolve_bearer_token(args.token)

    try:
        anyio.run(run, args.url, bearer_token)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
