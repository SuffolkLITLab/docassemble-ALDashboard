import uuid
import base64
import json

from flask import Response, jsonify, request
from flask_cors import cross_origin

from docassemble.webapp.app_object import app, csrf
from docassemble.webapp.server import api_verify, jsonify_with_status

from .mcp_registry import (
    MCP_API_BASE_PATH,
    get_discovered_tools,
    handle_jsonrpc_request,
)


def _auth_fail(request_id: str):
    return jsonify_with_status(
        {
            "success": False,
            "request_id": request_id,
            "error": {"type": "auth_error", "message": "Access denied."},
        },
        403,
    )


@app.route(MCP_API_BASE_PATH, methods=["POST", "GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["POST", "GET", "HEAD"], automatic_options=True)
def mcp_entrypoint():
    request_id = str(uuid.uuid4())
    if not api_verify():
        return _auth_fail(request_id)

    if request.method == "GET":
        return jsonify(
            {
                "success": True,
                "api_version": "v1",
                "request_id": request_id,
                "kind": "mcp",
                "endpoint": MCP_API_BASE_PATH,
                "transport": "json-rpc-2.0-over-http",
                "methods": ["initialize", "ping", "tools/list", "tools/call"],
            }
        )

    payload = request.get_json(silent=True)
    response_obj, status_code = handle_jsonrpc_request(payload)
    if (
        isinstance(payload, dict)
        and payload.get("method") == "tools/call"
        and isinstance(response_obj, dict)
        and isinstance(response_obj.get("result"), dict)
        and isinstance(response_obj["result"].get("toolCall"), dict)
    ):
        response_obj = _run_mcp_tool_call(response_obj)
    if response_obj is None:
        return Response("", status=status_code)
    return jsonify_with_status(response_obj, status_code)


def _forward_auth_headers() -> dict:
    headers = {}
    x_api_key = request.headers.get("X-API-Key")
    if x_api_key:
        headers["X-API-Key"] = x_api_key
    authorization = request.headers.get("Authorization")
    if authorization:
        headers["Authorization"] = authorization
    return headers


def _run_mcp_tool_call(response_obj: dict) -> dict:
    tool_call = response_obj["result"]["toolCall"]
    method = str(tool_call.get("method", "GET")).upper()
    path = str(tool_call.get("path", ""))
    arguments = tool_call.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}

    call_kwargs = {
        "path": path,
        "method": method,
        "headers": _forward_auth_headers(),
    }
    if method in {"GET", "DELETE", "HEAD", "OPTIONS"}:
        call_kwargs["query_string"] = arguments
    else:
        call_kwargs["json"] = arguments

    with app.test_client() as client:
        upstream = client.open(**call_kwargs)

    upstream_json = upstream.get_json(silent=True)
    if upstream_json is not None:
        structured = {
            "upstream": {
                "method": method,
                "path": path,
                "status": upstream.status_code,
            },
            "data": upstream_json,
        }
        text = json.dumps(upstream_json, ensure_ascii=True)
    else:
        raw_bytes = upstream.get_data()
        structured = {
            "upstream": {
                "method": method,
                "path": path,
                "status": upstream.status_code,
                "mimetype": upstream.mimetype,
            },
            "data": {
                "body_base64": base64.b64encode(raw_bytes).decode("ascii"),
                "size_bytes": len(raw_bytes),
            },
        }
        text = (
            f"Binary response {upstream.status_code} {upstream.mimetype} "
            f"({len(raw_bytes)} bytes, base64 in structuredContent.data.body_base64)"
        )

    response_obj["result"] = {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured,
        "isError": upstream.status_code >= 400,
    }
    return response_obj


@app.route(f"{MCP_API_BASE_PATH}/tools", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def mcp_tools():
    request_id = str(uuid.uuid4())
    if not api_verify():
        return _auth_fail(request_id)
    tools = get_discovered_tools()
    return jsonify(
        {
            "success": True,
            "api_version": "v1",
            "request_id": request_id,
            "count": len(tools),
            "tools": tools,
        }
    )


@app.route(f"{MCP_API_BASE_PATH}/docs", methods=["GET"])
@csrf.exempt
@cross_origin(origins="*", methods=["GET", "HEAD"], automatic_options=True)
def mcp_docs():
    return Response(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AL MCP Bridge Docs</title>
  <style>
    body {{
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      margin: 2rem auto;
      max-width: 860px;
      line-height: 1.45;
      padding: 0 1rem;
      color: #1f2937;
      background: linear-gradient(180deg, #f8fafc, #ffffff);
    }}
    code {{ background: #f1f5f9; padding: 0.1rem 0.3rem; border-radius: 4px; }}
    pre {{
      background: #0f172a;
      color: #e2e8f0;
      padding: 1rem;
      border-radius: 8px;
      overflow: auto;
    }}
  </style>
</head>
<body>
  <h1>AL MCP Bridge</h1>
  <p><strong>Endpoint:</strong> <code>POST {MCP_API_BASE_PATH}</code></p>
  <p><strong>Protocol:</strong> JSON-RPC 2.0 over HTTP</p>
  <p><strong>Auth:</strong> Uses docassemble API key authentication (<code>api_verify()</code>)</p>
  <h2>Supported methods</h2>
  <ul>
    <li><code>initialize</code></li>
    <li><code>ping</code></li>
    <li><code>tools/list</code></li>
    <li><code>tools/call</code></li>
  </ul>
  <h2>Example</h2>
  <pre>curl -X POST \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: &lt;DOCASSEMBLE_API_KEY&gt;" \\
  -d '{{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{{}}}}' \\
  {MCP_API_BASE_PATH}</pre>
  <p><strong>Tool execution auth:</strong> <code>tools/call</code> forwards the same incoming API auth headers to ALDashboard/ALWeaver REST routes.</p>
  <p><strong>ALWeaver discovery:</strong> listed by default only when <code>docassemble.ALWeaver</code> is installed. For development fallback, set <code>ALDASHBOARD_MCP_DEV_MODE=true</code> (and optionally <code>ALWEAVER_REPO_PATH</code>).</p>
  <p>Convenience listing endpoint: <code>GET {MCP_API_BASE_PATH}/tools</code></p>
</body>
</html>""",
        mimetype="text/html",
    )
