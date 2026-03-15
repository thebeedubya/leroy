"""Codex bus MCP client.

FastMCP STDIO server exposing simple FORGE bus tools for Codex.
"""

from typing import Any

import httpx
from fastmcp import FastMCP


import os

BUS_URL = os.environ.get("CODEX_BUS_URL", "http://localhost:9800")
BUS_TOKEN = os.environ.get("CODEX_BUS_TOKEN", "")
AGENT_NAME = "codex"


mcp = FastMCP("codex-bus")


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {BUS_TOKEN}",
    }


def _bus_url(path: str) -> str:
    return f"{BUS_URL.rstrip('/')}{path}"


async def _request(method: str, path: str, json_body: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.request(
            method,
            _bus_url(path),
            headers=_headers(),
            json=json_body,
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        return response.text


@mcp.tool()
async def bus_check_inbox() -> Any:
    """Return unread bus messages for Codex."""
    return await _request("GET", f"/messages?to={AGENT_NAME}&unread=true")


@mcp.tool()
async def bus_mark_read(message_id: str) -> Any:
    """Mark a bus message as read for Codex."""
    return await _request(
        "POST",
        f"/messages/{message_id}/read",
        {"agent": AGENT_NAME},
    )


@mcp.tool()
async def bus_respond(message_id: str, content: str) -> Any:
    """Respond to a bus message as Codex."""
    return await _request(
        "POST",
        f"/messages/{message_id}/respond",
        {
            "from": AGENT_NAME,
            "content": content,
        },
    )


@mcp.tool()
async def bus_send(to: str, msg_type: str, content: str, task_id: str = "") -> Any:
    """Send a bus message from Codex."""
    payload: dict[str, Any] = {
        "from": AGENT_NAME,
        "to": to,
        "type": msg_type,
        "content": content,
    }
    if task_id:
        payload["task_id"] = task_id
    return await _request("POST", "/messages", payload)


if __name__ == "__main__":
    mcp.run()
