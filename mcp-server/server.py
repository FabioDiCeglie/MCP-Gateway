"""Minimal upstream MCP server for local gateway smoke tests."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo-server", host="0.0.0.0", port=8000)


@mcp.tool()
def echo(message: str) -> str:
    """Echo a message back to the caller."""
    return message


@mcp.tool()
def ping() -> str:
    """Return a fixed pong response."""
    return "pong"


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
