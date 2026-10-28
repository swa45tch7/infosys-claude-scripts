"""Minimal stdio MCP server so Claude Desktop / Claude Code can call the tools."""

from __future__ import annotations

import json
import sys
from typing import Any

from lm_infosys.client import ApiError, ConfigError, load_dotenv
from lm_infosys.cli import (
    cmd_alerts,
    cmd_collectors,
    cmd_device,
    cmd_devices,
    cmd_health,
)


TOOLS = [
    {
        "name": "lm_health",
        "description": "Verify LogicMonitor API token and portal reachability.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "lm_alerts",
        "description": "List current LogicMonitor alerts (read-only).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "size": {"type": "integer", "default": 20},
                "severity": {"type": "string", "enum": ["warn", "error", "critical"]},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "lm_devices",
        "description": "Search or list LogicMonitor devices (read-only).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "size": {"type": "integer", "default": 25},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "lm_collectors",
        "description": "List LogicMonitor collectors (read-only).",
        "inputSchema": {
            "type": "object",
            "properties": {"size": {"type": "integer", "default": 25}},
            "additionalProperties": False,
        },
    },
    {
        "name": "lm_device",
        "description": "Show one LogicMonitor device by id or name (read-only).",
        "inputSchema": {
            "type": "object",
            "properties": {"identifier": {"type": "string"}},
            "required": ["identifier"],
            "additionalProperties": False,
        },
    },
]


class _Args:
    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


def _run_tool(name: str, arguments: dict[str, Any]) -> str:
    load_dotenv()
    if name == "lm_health":
        cmd_health(_Args())
        return "ok"
    if name == "lm_alerts":
        cmd_alerts(
            _Args(
                size=int(arguments.get("size") or 20),
                severity=arguments.get("severity"),
                json=False,
            )
        )
        return "ok"
    if name == "lm_devices":
        cmd_devices(
            _Args(
                query=arguments.get("query"),
                size=int(arguments.get("size") or 25),
                json=False,
            )
        )
        return "ok"
    if name == "lm_collectors":
        cmd_collectors(_Args(size=int(arguments.get("size") or 25), json=False))
        return "ok"
    if name == "lm_device":
        cmd_device(_Args(identifier=str(arguments.get("identifier") or ""), json=False))
        return "ok"
    raise ValueError(f"Unknown tool: {name}")


def _result(request_id: Any, text: str, is_error: bool = False) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        },
    }


def _handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "lm-infosys", "version": "1.0.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            from io import StringIO
            from contextlib import redirect_stdout, redirect_stderr

            buffer = StringIO()
            err = StringIO()
            with redirect_stdout(buffer), redirect_stderr(err):
                _run_tool(str(name), arguments)
            text = buffer.getvalue() + err.getvalue()
            return _result(request_id, text.strip() or "ok")
        except ConfigError as exc:
            return _result(request_id, str(exc), is_error=True)
        except ApiError as exc:
            return _result(request_id, str(exc), is_error=True)
        except Exception as exc:  # noqa: BLE001 - MCP tool boundary
            return _result(request_id, f"{type(exc).__name__}: {exc}", is_error=True)
    if request_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> int:
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        reply = _handle(message)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
