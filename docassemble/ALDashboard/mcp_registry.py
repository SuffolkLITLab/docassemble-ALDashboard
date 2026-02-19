import ast
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .api_dashboard_utils import build_openapi_spec as build_dashboard_openapi_spec

MCP_API_BASE_PATH = "/al/api/v1/mcp"
SUPPORTED_PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "AL MCP Bridge"
DEFAULT_ALWEAVER_REPO = os.path.expanduser("~/docassemble-ALWeaver")


def _normalize_path(path: str) -> str:
    return re.sub(r"<([^>]+)>", r"{\1}", path)


def _sanitize_for_tool_name(path: str) -> str:
    slug = path.strip("/")
    slug = slug.replace("{", "").replace("}", "")
    slug = slug.replace("<", "").replace(">", "")
    slug = re.sub(r"[^a-zA-Z0-9/_-]+", "-", slug)
    slug = slug.replace("/", "_").replace("-", "_")
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "root"


def _pick_input_schema(operation: Dict[str, Any]) -> Dict[str, Any]:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return {"type": "object", "additionalProperties": True}
    content = request_body.get("content")
    if not isinstance(content, dict):
        return {"type": "object", "additionalProperties": True}

    json_schema = content.get("application/json", {}).get("schema")
    if isinstance(json_schema, dict):
        return json_schema

    form_schema = content.get("multipart/form-data", {}).get("schema")
    if isinstance(form_schema, dict):
        return form_schema

    first_content = next(iter(content.values()), None)
    if isinstance(first_content, dict) and isinstance(
        first_content.get("schema"), dict
    ):
        return first_content["schema"]

    return {"type": "object", "additionalProperties": True}


def _openapi_to_tool_entries(
    spec: Dict[str, Any], namespace: str
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    paths = spec.get("paths", {})
    if not isinstance(paths, dict):
        return entries

    used_names = set()
    for raw_path, path_item in sorted(paths.items()):
        if not isinstance(path_item, dict):
            continue
        normalized_path = _normalize_path(str(raw_path))
        for method, operation in sorted(path_item.items()):
            if method.lower() not in {
                "get",
                "post",
                "put",
                "patch",
                "delete",
                "head",
                "options",
            }:
                continue
            if not isinstance(operation, dict):
                operation = {}
            summary = str(operation.get("summary", "")).strip()
            description = str(operation.get("description", "")).strip()
            if summary and description:
                description_text = f"{summary}. {description}"
            else:
                description_text = summary or description or "REST endpoint"

            base_name = f"{namespace}.{method.lower()}_{_sanitize_for_tool_name(normalized_path)}"
            name = base_name
            i = 2
            while name in used_names:
                name = f"{base_name}_{i}"
                i += 1
            used_names.add(name)

            entries.append(
                {
                    "name": name,
                    "description": f"{description_text} ({method.upper()} {normalized_path})",
                    "inputSchema": _pick_input_schema(operation),
                    "method": method.upper(),
                    "pathTemplate": normalized_path,
                    "namespace": namespace,
                }
            )

    return entries


def openapi_to_mcp_tools(spec: Dict[str, Any], namespace: str) -> List[Dict[str, Any]]:
    entries = _openapi_to_tool_entries(spec, namespace)
    return [
        {
            "name": entry["name"],
            "description": entry["description"],
            "inputSchema": entry["inputSchema"],
        }
        for entry in entries
    ]


def _extract_base_path_from_api_utils(api_utils_path: str) -> str:
    try:
        with open(api_utils_path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=api_utils_path)
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if target.id != "WEAVER_API_BASE_PATH":
                continue
            if isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, str
            ):
                return node.value.value
    except Exception:
        pass
    return "/al/api/v1/weaver"


def _eval_route_path_expr(node: ast.AST, base_path: str) -> Optional[str]:
    if isinstance(node, ast.Name) and node.id == "WEAVER_API_BASE_PATH":
        return base_path
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        chunks: List[str] = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                chunks.append(part.value)
                continue
            if isinstance(part, ast.FormattedValue) and isinstance(
                part.value, ast.Name
            ):
                if part.value.id == "WEAVER_API_BASE_PATH":
                    chunks.append(base_path)
                    continue
            return None
        return "".join(chunks)
    return None


def _parse_weaver_routes_from_repo(repo_root: str) -> Dict[str, Any]:
    api_utils_path = os.path.join(repo_root, "docassemble", "ALWeaver", "api_utils.py")
    api_weaver_path = os.path.join(
        repo_root, "docassemble", "ALWeaver", "api_weaver.py"
    )
    base_path = _extract_base_path_from_api_utils(api_utils_path)
    paths: Dict[str, Dict[str, Dict[str, str]]] = {}

    try:
        with open(api_weaver_path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=api_weaver_path)
    except Exception:
        return {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "app"
                and func.attr == "route"
            ):
                continue
            if not dec.args:
                continue
            route_path = _eval_route_path_expr(dec.args[0], base_path=base_path)
            if not route_path:
                continue

            methods = ["GET"]
            for keyword in dec.keywords:
                if keyword.arg != "methods":
                    continue
                if isinstance(keyword.value, ast.List):
                    parsed_methods = []
                    for method_node in keyword.value.elts:
                        if isinstance(method_node, ast.Constant) and isinstance(
                            method_node.value, str
                        ):
                            parsed_methods.append(method_node.value.upper())
                    if parsed_methods:
                        methods = parsed_methods

            normalized_path = _normalize_path(route_path)
            path_item = paths.setdefault(normalized_path, {})
            for method in methods:
                path_item[method.lower()] = {"summary": f"ALWeaver {method} endpoint"}

    return {
        "openapi": "3.1.0",
        "info": {"title": "ALWeaver API (discovered)", "version": "1.0.0"},
        "paths": paths,
    }


def _fallback_weaver_spec(base_path: str = "/al/api/v1/weaver") -> Dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "ALWeaver API (fallback)", "version": "1.0.0"},
        "paths": {
            base_path: {"post": {"summary": "Generate interview artifacts"}},
            f"{base_path}/jobs/{{job_id}}": {
                "get": {"summary": "Get async job status"},
                "delete": {"summary": "Delete async job metadata"},
            },
            f"{base_path}/openapi.json": {"get": {"summary": "Get OpenAPI document"}},
            f"{base_path}/docs": {"get": {"summary": "Human-readable docs"}},
        },
    }


def _parse_bool_env(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _weaver_dev_mode_enabled() -> bool:
    return _parse_bool_env(os.environ.get("ALDASHBOARD_MCP_DEV_MODE"))


def get_weaver_openapi_spec() -> Optional[Dict[str, Any]]:
    try:
        from docassemble.ALWeaver.api_utils import (  # type: ignore[import-untyped]
            build_openapi_spec as weaver_openapi,
        )

        spec = weaver_openapi()
        if isinstance(spec, dict) and isinstance(spec.get("paths"), dict):
            return spec
    except Exception:
        pass

    if not _weaver_dev_mode_enabled():
        return None

    repo_root = os.path.expanduser(
        os.environ.get("ALWEAVER_REPO_PATH", DEFAULT_ALWEAVER_REPO)
    )
    parsed = _parse_weaver_routes_from_repo(repo_root)
    if parsed.get("paths"):
        return parsed
    return _fallback_weaver_spec() if _weaver_dev_mode_enabled() else None


def get_discovered_tools() -> List[Dict[str, Any]]:
    entries = get_discovered_tool_entries()
    return [
        {
            "name": entry["name"],
            "description": entry["description"],
            "inputSchema": entry["inputSchema"],
        }
        for entry in entries
    ]


def get_discovered_tool_entries() -> List[Dict[str, Any]]:
    dashboard_spec = build_dashboard_openapi_spec()
    weaver_spec = get_weaver_openapi_spec()
    entries = []
    entries.extend(_openapi_to_tool_entries(dashboard_spec, namespace="aldashboard"))
    if isinstance(weaver_spec, dict):
        entries.extend(_openapi_to_tool_entries(weaver_spec, namespace="alweaver"))
    return entries


def get_tool_entry_by_name(name: str) -> Optional[Dict[str, Any]]:
    for entry in get_discovered_tool_entries():
        if entry["name"] == name:
            return entry
    return None


def _split_path_and_query_args(
    path_template: str, arguments: Dict[str, Any]
) -> Tuple[str, Dict[str, Any], List[str]]:
    remaining = dict(arguments)
    missing_params: List[str] = []

    def _replace(match: re.Match) -> str:
        param = match.group(1)
        if param not in remaining:
            missing_params.append(param)
            return match.group(0)
        value = remaining.pop(param)
        return str(value)

    resolved_path = re.sub(r"{([^}]+)}", _replace, path_template)
    return resolved_path, remaining, missing_params


def _jsonrpc_error(error_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": error_id,
        "error": {"code": code, "message": message},
    }


def _jsonrpc_result(result_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": result_id, "result": result}


def handle_jsonrpc_request(
    payload: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], int]:
    if not isinstance(payload, dict):
        return _jsonrpc_error(None, -32600, "Invalid Request"), 200

    if payload.get("jsonrpc") != "2.0":
        return _jsonrpc_error(payload.get("id"), -32600, "Invalid Request"), 200

    method = payload.get("method")
    if not isinstance(method, str) or not method.strip():
        return _jsonrpc_error(payload.get("id"), -32600, "Invalid Request"), 200

    request_id = payload.get("id")
    is_notification = request_id is None
    params = payload.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _jsonrpc_error(request_id, -32602, "Invalid params"), 200

    if method == "initialize":
        client_version = params.get("protocolVersion")
        if isinstance(client_version, str) and client_version.strip():
            protocol_version = client_version
        else:
            protocol_version = SUPPORTED_PROTOCOL_VERSION
        if is_notification:
            return None, 204
        return (
            _jsonrpc_result(
                request_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": "1.0.0"},
                },
            ),
            200,
        )

    if method in {"notifications/initialized", "initialized"}:
        if is_notification:
            return None, 204
        return _jsonrpc_result(request_id, {}), 200

    if method == "ping":
        if is_notification:
            return None, 204
        return _jsonrpc_result(request_id, {}), 200

    if method == "tools/list":
        if is_notification:
            return None, 204
        return _jsonrpc_result(request_id, {"tools": get_discovered_tools()}), 200

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") if "arguments" in params else {}
        if not isinstance(name, str) or not name.strip():
            return (
                _jsonrpc_error(request_id, -32602, "tools/call requires tool name"),
                200,
            )
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return (
                _jsonrpc_error(
                    request_id, -32602, "tools/call arguments must be an object"
                ),
                200,
            )

        tool_entry = get_tool_entry_by_name(name)
        if tool_entry is None:
            return _jsonrpc_error(request_id, -32601, f"Unknown tool: {name}"), 200

        resolved_path, remaining_args, missing = _split_path_and_query_args(
            tool_entry["pathTemplate"], arguments
        )
        if missing:
            missing_csv = ", ".join(sorted(missing))
            return (
                _jsonrpc_error(
                    request_id,
                    -32602,
                    f"Missing required path parameters for {name}: {missing_csv}",
                ),
                200,
            )

        if is_notification:
            return None, 204
        return (
            _jsonrpc_result(
                request_id,
                {
                    "toolCall": {
                        "name": name,
                        "method": tool_entry["method"],
                        "path": resolved_path,
                        "arguments": remaining_args,
                    }
                },
            ),
            200,
        )

    return _jsonrpc_error(request_id, -32601, f"Method not found: {method}"), 200
