"""Smoke-test client: initialize + tools/list through the gateway."""

from __future__ import annotations

import argparse
import sys

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

DEFAULT_GATEWAY_URL = "http://127.0.0.1:8080/mcp"


async def run(url: str) -> None:
    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            print(f"Connected to {url}")
            print(f"Tools: {', '.join(names)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP gateway smoke-test client")
    parser.add_argument(
        "--url",
        default=DEFAULT_GATEWAY_URL,
        help=f"Gateway MCP endpoint (default: {DEFAULT_GATEWAY_URL})",
    )
    args = parser.parse_args()

    try:
        anyio.run(run, args.url)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
