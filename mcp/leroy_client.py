"""Leroy MCP Client -- PM tools for sending specs and managing tasks.

FastMCP STDIO server that PM (Claude CLI) uses to communicate with
the Leroy A2A server on localhost:9800.

Sends specs, polls for completion, returns results. PM never leaves
their terminal.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

import httpx
from fastmcp import FastMCP

import config

mcp = FastMCP("leroy-mcp")

# ---------------------------------------------------------------------------
# Spec repository helpers
# ---------------------------------------------------------------------------
_SPECS_DIR = Path.home() / "Projects" / "leroy" / "specs"


def _specs_dir() -> Path:
    """Return the specs directory, creating it if needed."""
    _SPECS_DIR.mkdir(parents=True, exist_ok=True)
    return _SPECS_DIR


def _derive_slug(subject: str) -> str:
    """Derive a filesystem-safe slug from a subject string."""
    if not subject or not subject.strip():
        return "untitled"
    slug = subject.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug.strip())
    slug = re.sub(r"-+", "-", slug)
    return slug[:50].rstrip("-") or "untitled"


def _unique_spec_path(today: str, slug: str) -> Path:
    """Return a unique path for a new spec file, appending counter if needed."""
    d = _specs_dir()
    candidate = d / f"{today}-{slug}.md"
    if not candidate.exists():
        return candidate
    counter = 2
    while True:
        candidate = d / f"{today}-{slug}-{counter}.md"
        if not candidate.exists():
            return candidate
        counter += 1


def _parse_frontmatter(content: str) -> dict:
    """Parse simple YAML front matter (key: value lines) from a markdown file.

    Returns a dict of the front matter fields, or {} if none found.
    """
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end == -1:
        return {}
    fm_block = content[3:end].strip()
    result = {}
    for line in fm_block.splitlines():
        m = re.match(r"^(\w[\w_-]*):\s*(.*)", line)
        if m:
            result[m.group(1)] = m.group(2).strip()
    return result


def _update_frontmatter(content: str, updates: dict) -> str:
    """Update specific keys in the YAML front matter block of a markdown file.

    Only updates keys that already exist in the front matter.
    """
    if not content.startswith("---"):
        return content
    end = content.find("\n---", 3)
    if end == -1:
        return content

    fm_block = content[3:end]
    for key, value in updates.items():
        # Replace the value for an existing key
        fm_block = re.sub(
            rf"^({re.escape(key)}:\s*).*$",
            rf"\g<1>{value}",
            fm_block,
            flags=re.MULTILINE,
        )

    return "---" + fm_block + content[end:]


def _get_recent_spec_files(n: int = 10) -> list[Path]:
    """Return up to N most recent spec .md files, sorted by filename (descending).

    Ignores .gitkeep and non-md files.
    """
    d = _specs_dir()
    files = sorted(
        [f for f in d.glob("*.md") if f.name != ".gitkeep"],
        key=lambda f: f.name,
        reverse=True,
    )
    return files[:n]


def _format_recent_specs_summary(files: list[Path]) -> str:
    """Format a one-line-per-spec summary from a list of spec files."""
    if not files:
        return "No past specs found."

    lines = [f"Recent specs ({len(files)}):"]
    for f in files:
        try:
            content = f.read_text(encoding="utf-8")
        except Exception:
            continue
        fm = _parse_frontmatter(content)
        spec_id = fm.get("spec_id", f.stem)
        dt = fm.get("date", "unknown")
        status = fm.get("status", "unknown")
        pass_rate = fm.get("pass_rate", "(pending)")
        retro = fm.get("retrospective", "(pending)")
        retro_preview = retro[:80] if retro else ""
        lines.append(
            f"  {dt} | {spec_id:<50} | {status:<10} | pass={pass_rate} | {retro_preview}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _headers() -> dict[str, str]:
    """Build request headers with auth token."""
    headers = {"Content-Type": "application/json"}
    if config.LEROY_A2A_TOKEN:
        headers["Authorization"] = f"Bearer {config.LEROY_A2A_TOKEN}"
    return headers


def _a2a_url() -> str:
    return config.LEROY_A2A_URL.rstrip("/")


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------
@mcp.tool()
async def leroy_send_spec(spec: str, subject: str = "") -> str:
    """Send an engineering spec to Leroy for execution.

    Args:
        spec: The full spec text (markdown). Include objective, scope,
              success criteria, constraints, and machine details.
        subject: Optional short subject line for the spec.

    Returns:
        Task ID and confirmation message.
    """
    today = date.today().isoformat()
    slug = _derive_slug(subject)
    spec_path = _unique_spec_path(today, slug)

    # Build front matter
    front_matter = (
        "---\n"
        f"spec_id: {slug}\n"
        f"task_id: (pending)\n"
        f"date: {today}\n"
        f"status: sent\n"
        f"pass_rate: (pending)\n"
        f"retrospective: (pending)\n"
        f"tags: []\n"
        "---\n\n"
    )

    # Read 10 most recent specs BEFORE saving this one
    recent_files = _get_recent_spec_files(10)
    recent_summary = _format_recent_specs_summary(recent_files)

    # Save spec to disk
    spec_path.write_text(front_matter + spec, encoding="utf-8")

    # Send to A2A
    message_id = uuid4().hex
    payload = {
        "jsonrpc": "2.0",
        "id": message_id,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": spec}],
                "messageId": message_id,
            }
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_a2a_url()}/",
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        return f"Error: {data['error'].get('message', 'Unknown error')}"

    # Extract task_id from A2A response text
    result = data.get("result", {})
    parts = result.get("parts", [])
    response_text = ""
    for part in parts:
        if part.get("kind") == "text":
            response_text += part.get("text", "")

    # Parse task_id from response (format: "Task <id> received and executing.")
    task_id = None
    if "Task " in response_text:
        task_id = response_text.split("Task ")[1].split(" ")[0]

    if not task_id:
        return f"Spec sent but could not extract task ID. Response: {response_text}"

    # Update saved spec file with real task_id
    try:
        content = spec_path.read_text(encoding="utf-8")
        updated = _update_frontmatter(content, {"task_id": task_id})
        spec_path.write_text(updated, encoding="utf-8")
    except Exception as e:
        # Non-fatal: spec is saved, task_id update failed
        pass

    subject_line = f" ({subject})" if subject else ""
    return (
        f"Spec sent to Leroy{subject_line}. Task ID: {task_id}\n"
        f"Spec saved: {spec_path.name}\n"
        f"Leroy is working on it. Check progress with: leroy_check_task('{task_id}')\n\n"
        f"{recent_summary}"
    )


@mcp.tool()
async def leroy_update_spec(task_id: str, pass_rate: str, retrospective: str) -> str:
    """Update a spec file with QA outcome and retrospective.

    Call this after QA completes to record results against the original spec.

    Args:
        task_id: The task ID from leroy_send_spec (used to find the spec file).
        pass_rate: QA pass rate string, e.g. '5/5' or '3/5 (2 failures: ...).
        retrospective: Free-text retrospective written by PM after QA.

    Returns:
        Confirmation with filename, or error if not found.
    """
    d = _specs_dir()
    target_file: Path | None = None

    for f in d.glob("*.md"):
        if f.name == ".gitkeep":
            continue
        try:
            content = f.read_text(encoding="utf-8")
        except Exception:
            continue
        fm = _parse_frontmatter(content)
        if fm.get("task_id") == task_id:
            target_file = f
            break

    if target_file is None:
        return (
            f"No spec file found with task_id: {task_id}\n"
            f"Searched {d}. Either the file was never created or the task_id is wrong."
        )

    content = target_file.read_text(encoding="utf-8")

    # Determine status from pass_rate
    pr_lower = pass_rate.lower()
    if re.search(r"\b0/", pr_lower) or "fail" in pr_lower:
        new_status = "failed"
    else:
        new_status = "completed"

    # Escape retrospective for single-line front matter (strip newlines)
    retro_oneliner = retrospective.replace("\n", " ").replace("\r", "").strip()

    # Update front matter
    content = _update_frontmatter(content, {
        "status": new_status,
        "pass_rate": pass_rate,
        "retrospective": retro_oneliner,
    })

    # Append outcome section
    outcome_block = (
        f"\n---\n"
        f"## Outcome\n"
        f"**Task ID:** {task_id}\n"
        f"**QA pass rate:** {pass_rate}\n\n"
        f"## Retrospective\n"
        f"{retrospective}\n"
    )
    content = content + outcome_block

    target_file.write_text(content, encoding="utf-8")

    return (
        f"Spec updated: {target_file.name}\n"
        f"Status: {new_status}\n"
        f"Pass rate: {pass_rate}\n"
        f"Retrospective recorded."
    )


@mcp.tool()
async def leroy_read_recent_specs(n: int = 10) -> str:
    """Read metadata from the N most recent spec files.

    Args:
        n: Number of recent specs to return (default 10).

    Returns:
        Formatted summary with one line per spec: id, date, status, pass_rate, retrospective.
    """
    files = _get_recent_spec_files(n)
    return _format_recent_specs_summary(files)


@mcp.tool()
async def leroy_check_task(task_id: str) -> str:
    """Check the status and result of a Leroy task.

    Args:
        task_id: The task ID returned by leroy_send_spec.

    Returns:
        Task status, spec summary, and result (if completed).
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{_a2a_url()}/tasks/{task_id}",
            headers=_headers(),
        )
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        return f"Error: {data['error']}"

    status = data.get("status", "unknown")
    spec = data.get("spec", "")
    result = data.get("result")

    lines = [
        f"Task: {task_id}",
        f"Status: {status}",
        f"Created: {data.get('created_at', 'unknown')}",
    ]

    if spec:
        # Show first 200 chars of spec
        preview = spec[:200] + "..." if len(spec) > 200 else spec
        lines.append(f"Spec preview: {preview}")

    if result:
        lines.append(f"Result: {result}")

    if data.get("completed_at"):
        lines.append(f"Completed: {data['completed_at']}")

    return "\n".join(lines)


@mcp.tool()
async def leroy_list_tasks(status: str = "") -> str:
    """List all tasks in the Leroy A2A server.

    Args:
        status: Optional filter: 'pending', 'completed', 'cancelled'.
                Empty string returns all tasks.

    Returns:
        Summary of all matching tasks.
    """
    url = f"{_a2a_url()}/tasks"
    if status:
        url += f"?status={status}"

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        data = resp.json()

    tasks = data.get("tasks", [])
    if not tasks:
        return f"No tasks found{f' with status={status}' if status else ''}."

    lines = [f"Tasks ({data.get('count', len(tasks))}):", ""]
    for t in tasks:
        spec_preview = t.get("spec", "")[:80]
        if len(t.get("spec", "")) > 80:
            spec_preview += "..."
        lines.append(
            f"  [{t['status'].upper():>10}] {t['task_id'][:12]}... "
            f"| {spec_preview}"
        )

    return "\n".join(lines)


@mcp.tool()
async def leroy_cancel_task(task_id: str) -> str:
    """Cancel a pending or in-progress Leroy task.

    Args:
        task_id: The task ID to cancel.

    Returns:
        Confirmation or error message.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{_a2a_url()}/tasks/{task_id}/cancel",
            headers=_headers(),
        )
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        return f"Error: {data['error']}"

    return f"Task {task_id} cancelled."


@mcp.tool()
async def leroy_read_messages(pending_only: bool = True) -> str:
    """Read messages from Leroy (questions, blockers, decision gates, status updates).

    Call this when you see a desktop notification or want to check if Leroy
    has sent anything. By default shows only messages that need your response.

    Args:
        pending_only: If True (default), show only messages awaiting PM response.
                      If False, show all recent messages.

    Returns:
        List of messages with IDs, types, and content.
    """
    endpoint = "/pm/messages/pending" if pending_only else "/pm/messages"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(
                f"{_a2a_url()}{endpoint}",
                headers=_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.ConnectError:
            return "Cannot reach Leroy A2A server. Is it running on port 9800?"
        except Exception as e:
            return f"Error reading messages: {e}"

    messages = data.get("messages", [])
    if not messages:
        label = "pending" if pending_only else "recent"
        return f"No {label} messages from Leroy."

    lines = [f"{'Pending messages' if pending_only else 'Recent messages'} ({len(messages)}):", ""]
    for msg in messages:
        msg_id = msg.get("message_id", "unknown")
        msg_type = msg.get("type", "unknown").upper()
        task_id = msg.get("task_id", "unknown")
        content = msg.get("content", "")
        options = msg.get("options", [])
        context = msg.get("context", "")
        requires_response = msg.get("requires_response", False)
        responded = msg.get("responded", False)
        ts = msg.get("received_at", msg.get("timestamp", ""))[:19].replace("T", " ")

        lines.append(f"[{msg_type}] message_id: {msg_id}")
        lines.append(f"  Task: {task_id}")
        lines.append(f"  Time: {ts}")
        lines.append(f"  Content: {content}")
        if context:
            lines.append(f"  Context: {context}")
        if options:
            lines.append(f"  Options: {', '.join(options)}")
        if requires_response and not responded:
            lines.append(f"  ** AWAITING YOUR RESPONSE -- use leroy_reply_to_message('{msg_id}', ...) **")
        elif responded:
            lines.append(f"  [RESPONDED: {msg.get('pm_response', '')}]")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def leroy_reply_to_message(message_id: str, response: str) -> str:
    """Reply to a message from Leroy.

    Use this to answer Leroy's questions, make decisions at decision gates,
    or unblock Leroy when it reports a blocker.

    Args:
        message_id: The message_id from leroy_read_messages().
        response: Your answer, decision, or guidance for Leroy.

    Returns:
        Confirmation that Leroy has been unblocked.
    """
    payload = {
        "response": response,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(
                f"{_a2a_url()}/pm/messages/{message_id}/respond",
                headers=_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.ConnectError:
            return "Cannot reach Leroy A2A server. Is it running on port 9800?"
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return f"Message {message_id} not found. Check the message_id from leroy_read_messages()."
            return f"Error sending response: {e}"
        except Exception as e:
            return f"Error sending response: {e}"

    if "error" in data:
        return f"Error: {data['error']}"

    task_id = data.get("task_id", "unknown")
    return (
        f"Response sent. Leroy has been unblocked.\n"
        f"Message ID: {message_id}\n"
        f"Task: {task_id}\n"
        f"Your response: {response}"
    )


@mcp.tool()
async def leroy_archive_task(task_id: str) -> str:
    """Archive a completed task to hide it from default list views.

    Archived tasks are never deleted -- they remain in the database and are
    fully queryable with leroy_list_tasks(status='completed') or via the
    dashboard. Use this to keep the default task list clean without losing history.

    Args:
        task_id: The task ID to archive.

    Returns:
        Confirmation or error message.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(
                f"{_a2a_url()}/tasks/{task_id}/archive",
                headers=_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.ConnectError:
            return "Cannot reach Leroy A2A server. Is it running on port 9800?"
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return f"Task {task_id} not found."
            return f"Error archiving task: {e}"
        except Exception as e:
            return f"Error archiving task: {e}"

    if "error" in data:
        return f"Error: {data['error']}"

    return f"Task {task_id} archived. It will no longer appear in default list views but is retained permanently."


@mcp.tool()
async def leroy_delete_task(task_id: str, confirm: bool = False, reason: str = "") -> str:
    """ADMIN ONLY: Permanently hard-delete a task from the database.

    This is irreversible. Tasks are the permanent record of what Leroy has built.
    Use leroy_archive_task() instead if you just want to hide old tasks from views.

    This tool requires confirm=True to actually execute. Without it, returns an error.

    Args:
        task_id: The task ID to permanently delete.
        confirm: Must be True to actually delete. False (default) returns a safety error.
        reason: Why this task is being deleted (required for audit log).

    Returns:
        Confirmation or error message.
    """
    if not confirm:
        return (
            "Deletion refused: confirm=True is required.\n"
            "Tasks are permanent records. Consider leroy_archive_task() instead.\n"
            "To actually delete, call leroy_delete_task(task_id, confirm=True, reason='your reason')."
        )

    if not reason:
        return "reason is required when confirm=True. Explain why this task is being deleted."

    payload = {"confirm": True, "reason": reason}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.request(
                "DELETE",
                f"{_a2a_url()}/tasks/{task_id}",
                headers=_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.ConnectError:
            return "Cannot reach Leroy A2A server. Is it running on port 9800?"
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return f"Task {task_id} not found."
            return f"Error deleting task: {e}"
        except Exception as e:
            return f"Error deleting task: {e}"

    if "error" in data:
        return f"Error: {data['error']}"

    return f"Task {task_id} permanently deleted. Reason logged: {reason}"


@mcp.tool()
async def leroy_health() -> str:
    """Check the health of the Leroy A2A server.

    Returns:
        Server health status including uptime and task counts.
    """
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            resp = await client.get(
                f"{config.LEROY_HEALTH_URL}/health",
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.ConnectError:
            return "Leroy A2A server is DOWN (connection refused)"
        except Exception as e:
            return f"Leroy A2A server health check failed: {e}"

    tasks = data.get("tasks", {})
    pm_msgs = data.get("pm_messages", {})
    persist = data.get("persistence", {})
    lines = [
        f"Status: {data.get('status', 'unknown')}",
        f"Version: {data.get('version', 'unknown')}",
        f"Uptime: {data.get('uptime_seconds', 0):.0f}s",
        f"Auth: {'enabled' if data.get('auth_enabled') else 'disabled'}",
        f"Tasks: {tasks.get('total', 0)} total, "
        f"{tasks.get('pending', 0)} pending, "
        f"{tasks.get('working', 0)} working, "
        f"{tasks.get('waiting_for_pm', 0)} waiting_for_pm, "
        f"{tasks.get('completed', 0)} completed, "
        f"{tasks.get('failed', 0)} failed",
    ]

    if pm_msgs:
        lines.append(
            f"PM Messages: {pm_msgs.get('pending_pm_response', 0)} awaiting response, "
            f"webhook={'registered' if pm_msgs.get('pm_webhook_registered') else 'offline'}"
        )

    if persist:
        lines.append(
            f"Brain: queue_depth={persist.get('queue_depth', '?')}"
        )

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
