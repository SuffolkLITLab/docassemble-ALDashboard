import unittest
import os
from unittest.mock import patch

from docassemble.ALDashboard.mcp_registry import (
    handle_jsonrpc_request,
    openapi_to_mcp_tools,
    _parse_weaver_routes_from_repo,
    get_weaver_openapi_spec,
    get_discovered_tool_entries,
)


class TestMCPRegistry(unittest.TestCase):
    def test_openapi_to_mcp_tools_builds_names_and_descriptions(self):
        spec = {
            "paths": {
                "/al/api/v1/example": {
                    "post": {
                        "summary": "Create example",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"name": {"type": "string"}},
                                    }
                                }
                            }
                        },
                    }
                },
                "/al/api/v1/example/{job_id}": {
                    "get": {"summary": "Get job"},
                    "delete": {"summary": "Delete job"},
                },
            }
        }
        tools = openapi_to_mcp_tools(spec, namespace="example")
        names = [tool["name"] for tool in tools]
        self.assertIn("example.post_al_api_v1_example", names)
        self.assertIn("example.get_al_api_v1_example_job_id", names)
        self.assertIn("example.delete_al_api_v1_example_job_id", names)
        post_tool = next(
            tool for tool in tools if tool["name"] == "example.post_al_api_v1_example"
        )
        self.assertEqual(post_tool["inputSchema"]["type"], "object")

    def test_handle_initialize_and_tools_list(self):
        init_response, init_status = handle_jsonrpc_request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertEqual(init_status, 200)
        self.assertEqual(
            init_response["result"]["capabilities"]["tools"]["listChanged"], False
        )

        with patch(
            "docassemble.ALDashboard.mcp_registry.get_discovered_tools",
            return_value=[
                {
                    "name": "sample.tool",
                    "description": "x",
                    "inputSchema": {"type": "object"},
                }
            ],
        ):
            tools_response, tools_status = handle_jsonrpc_request(
                {"jsonrpc": "2.0", "id": "abc", "method": "tools/list", "params": {}}
            )
        self.assertEqual(tools_status, 200)
        self.assertEqual(len(tools_response["result"]["tools"]), 1)
        self.assertEqual(tools_response["result"]["tools"][0]["name"], "sample.tool")

    def test_handle_tools_call_resolves_path_and_arguments(self):
        with patch(
            "docassemble.ALDashboard.mcp_registry.get_tool_entry_by_name",
            return_value={
                "name": "example.get_al_api_v1_example_job_id",
                "method": "GET",
                "pathTemplate": "/al/api/v1/example/{job_id}",
            },
        ):
            response, status = handle_jsonrpc_request(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "tools/call",
                    "params": {
                        "name": "example.get_al_api_v1_example_job_id",
                        "arguments": {"job_id": "abc123", "verbose": True},
                    },
                }
            )
        self.assertEqual(status, 200)
        tool_call = response["result"]["toolCall"]
        self.assertEqual(tool_call["method"], "GET")
        self.assertEqual(tool_call["path"], "/al/api/v1/example/abc123")
        self.assertEqual(tool_call["arguments"], {"verbose": True})

    def test_handle_tools_call_requires_missing_path_params(self):
        with patch(
            "docassemble.ALDashboard.mcp_registry.get_tool_entry_by_name",
            return_value={
                "name": "example.get_al_api_v1_example_job_id",
                "method": "GET",
                "pathTemplate": "/al/api/v1/example/{job_id}",
            },
        ):
            response, status = handle_jsonrpc_request(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "tools/call",
                    "params": {
                        "name": "example.get_al_api_v1_example_job_id",
                        "arguments": {"verbose": True},
                    },
                }
            )
        self.assertEqual(status, 200)
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("job_id", response["error"]["message"])

    def test_handle_invalid_jsonrpc(self):
        response, status = handle_jsonrpc_request({"id": 1, "method": "tools/list"})
        self.assertEqual(status, 200)
        self.assertEqual(response["error"]["code"], -32600)

    def test_parse_weaver_routes_from_repo(self):
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as td:
            api_dir = os.path.join(td, "docassemble", "ALWeaver")
            os.makedirs(api_dir, exist_ok=True)
            with open(
                os.path.join(api_dir, "api_utils.py"), "w", encoding="utf-8"
            ) as f:
                f.write('WEAVER_API_BASE_PATH = "/al/api/v1/weaver"\n')
            with open(
                os.path.join(api_dir, "api_weaver.py"), "w", encoding="utf-8"
            ) as f:
                f.write(
                    "\n".join(
                        [
                            "from docassemble.webapp.app_object import app",
                            '@app.route(WEAVER_API_BASE_PATH, methods=["POST"])',
                            "def a(): pass",
                            '@app.route(f"{WEAVER_API_BASE_PATH}/jobs/<job_id>", methods=["GET", "DELETE"] )',
                            "def b(job_id): pass",
                        ]
                    )
                )
            spec = _parse_weaver_routes_from_repo(td)
            self.assertIn("/al/api/v1/weaver", spec["paths"])
            self.assertIn("/al/api/v1/weaver/jobs/{job_id}", spec["paths"])
            self.assertIn("post", spec["paths"]["/al/api/v1/weaver"])
            self.assertIn("get", spec["paths"]["/al/api/v1/weaver/jobs/{job_id}"])

    @patch(
        "docassemble.ALDashboard.mcp_registry._weaver_dev_mode_enabled",
        return_value=False,
    )
    @patch("docassemble.ALDashboard.mcp_registry.build_dashboard_openapi_spec")
    @patch(
        "docassemble.ALDashboard.mcp_registry.get_weaver_openapi_spec",
        return_value=None,
    )
    def test_discovery_skips_weaver_when_missing_and_not_dev(
        self, _mock_weaver, mock_dashboard, _mock_dev
    ):
        mock_dashboard.return_value = {
            "paths": {"/al/api/v1/dashboard/docs": {"get": {"summary": "docs"}}}
        }
        entries = get_discovered_tool_entries()
        names = [entry["name"] for entry in entries]
        self.assertTrue(any(name.startswith("aldashboard.") for name in names))
        self.assertFalse(any(name.startswith("alweaver.") for name in names))

    @patch.dict(os.environ, {"ALDASHBOARD_MCP_DEV_MODE": "false"}, clear=False)
    def test_get_weaver_spec_none_when_not_installed_and_not_dev(self):
        with patch(
            "builtins.__import__",
            side_effect=lambda *a, **k: (_ for _ in ()).throw(ImportError()),
        ):
            spec = get_weaver_openapi_spec()
        self.assertIsNone(spec)


if __name__ == "__main__":
    unittest.main()
