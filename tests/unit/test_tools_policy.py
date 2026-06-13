from __future__ import annotations

import json

import pytest

from config import PolicyConfig
from services.tools_policy import POLICY_DENIED_CODE, ToolsPolicyService


class TestToolsPolicyService:
    """Policy allow-list: only echo may run; other tools are denied."""

    @pytest.fixture
    def service(self) -> ToolsPolicyService:
        return ToolsPolicyService(PolicyConfig(tools_allowed=["echo"]))

    # --- allow-list ---

    def test_echo_is_allowed(self, service: ToolsPolicyService) -> None:
        assert service.is_allowed("echo") is True

    def test_ping_is_not_allowed(self, service: ToolsPolicyService) -> None:
        assert service.is_allowed("ping") is False

    # --- parse_tool_call: only tools/call with a tool name counts ---

    def test_parse_ignores_tools_list(self) -> None:
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        ).encode()
        assert ToolsPolicyService.parse_tool_call(body) is None

    def test_parse_ignores_invalid_json(self) -> None:
        assert ToolsPolicyService.parse_tool_call(b"not json") is None

    def test_parse_ignores_tools_call_without_tool_name(self) -> None:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"arguments": {}},
            }
        ).encode()
        assert ToolsPolicyService.parse_tool_call(body) is None

    def test_parse_extracts_tool_name_and_request_id(self) -> None:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "req-1",
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {}},
            }
        ).encode()
        tool_call = ToolsPolicyService.parse_tool_call(body)
        assert tool_call is not None
        assert tool_call.tool_name == "echo"
        assert tool_call.request_id == "req-1"

    # --- check_post: pass through or deny ---

    def test_check_post_passes_allowed_tool(self, service: ToolsPolicyService) -> None:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {}},
            }
        ).encode()
        assert service.check_post(body) is None

    def test_check_post_denies_blocked_tool(self, service: ToolsPolicyService) -> None:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "ping", "arguments": {}},
            }
        ).encode()
        denial = service.check_post(body)
        assert denial is not None
        assert denial.status_code == 200

        error = json.loads(denial.body)["error"]
        assert error["code"] == POLICY_DENIED_CODE
        assert error["message"] == "Tool 'ping' denied by gateway policy"

    def test_check_post_ignores_non_tools_call(
        self, service: ToolsPolicyService
    ) -> None:
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        ).encode()
        assert service.check_post(body) is None
