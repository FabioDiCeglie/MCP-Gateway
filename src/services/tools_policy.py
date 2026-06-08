from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from config import PolicyConfig

POLICY_DENIED_CODE = -32000
JSON_HEADERS = {"content-type": "application/json"}


@dataclass(frozen=True)
class ToolsPolicyDenial:
    status_code: int
    headers: dict[str, str]
    body: bytes


class ToolsPolicyService:
    def __init__(self, policy: PolicyConfig) -> None:
        self._allowed_tools = set(policy.tools_allowed)

    def is_allowed(self, tool_name: str) -> bool:
        return tool_name in self._allowed_tools

    def check_post(self, body: bytes) -> ToolsPolicyDenial | None:
        """Inspect a POST body; return a denial response for blocked tools/call."""
        message = self._parse_json_rpc(body)
        if message is None:
            return None

        if message.get("method") != "tools/call":
            return None

        params = message.get("params")
        if not isinstance(params, dict):
            return None

        tool_name = params.get("name")
        if not isinstance(tool_name, str):
            return None

        if self.is_allowed(tool_name):
            return None

        return self._deny_tool_call(message.get("id"), tool_name)

    @staticmethod
    def _parse_json_rpc(body: bytes) -> dict[str, Any] | None:
        try:
            message = json.loads(body)
        except json.JSONDecodeError:
            return None

        if not isinstance(message, dict):
            return None
        return message

    @staticmethod
    def _deny_tool_call(request_id: Any, tool_name: str) -> ToolsPolicyDenial:
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": POLICY_DENIED_CODE,
                    "message": f"Tool '{tool_name}' denied by gateway policy",
                },
            }
        ).encode()
        return ToolsPolicyDenial(
            status_code=200,
            headers=dict(JSON_HEADERS),
            body=payload,
        )
