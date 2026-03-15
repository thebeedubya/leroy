"""Leroy MCP Client -- PM tools for sending specs and managing tasks.

FastMCP STDIO server that PM (Claude CLI) uses to communicate with
the Leroy A2A server on localhost:9800.

Sends specs, polls for completion, returns results. PM never leaves
their terminal.

v2 Phase 5A: Brain query integration in leroy_send_spec (check_before_act,
lessons attached to plan record).
"""
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

# Make sibling MCP modules and server-side shared modules importable when
# Codex launches this file directly as a script.
_MCP_DIR = Path(__file__).parent
_SERVER_DIR = _MCP_DIR.parent / "server"
sys.path.insert(0, str(_SERVER_DIR))
sys.path.insert(0, str(_MCP_DIR))

import httpx
from fastmcp import FastMCP

import config
from spec_analyzer import extract_typed_ir, check_dedup, check_complexity, check_preflight
from task_analytics import score_pre_send
import task_db
import persist_manager as pm

mcp = FastMCP("leroy-mcp")

# v2 Phase 5A: Shared PersistenceManager for brain queries from MCP client
_brain = pm.PersistenceManager()


def _get_plan_store() -> task_db.PlanStore:
    """Lazy-init task_db and return the plan store singleton."""
    if task_db.plan_store is None:
        task_db.init()
    return task_db.plan_store

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
    # ---------------------------------------------------------------------------
    # Retro gate: block submission if any completed specs are missing retros
    # ---------------------------------------------------------------------------
    override = "RETRO_OVERRIDE" in subject
    retro_debt: list[tuple[str, str]] = []  # (filename, task_id)

    for f in _get_recent_spec_files(20):
        try:
            fm_content = f.read_text(encoding="utf-8")
        except Exception:
            continue
        fm = _parse_frontmatter(fm_content)
        fm_status = fm.get("status", "")
        fm_retro = fm.get("retrospective", "")
        if fm_status == "completed" and (not fm_retro or fm_retro.strip() == "(pending)"):
            retro_debt.append((f.name, fm.get("task_id", "(unknown)")))

    if retro_debt and not override:
        debt_lines = "\n".join(
            f"  - {name} (task: {tid})" for name, tid in retro_debt
        )
        return (
            f"BLOCKED: {len(retro_debt)} spec(s) missing retrospectives. "
            f"Write retros before sending new specs.\n\n"
            f"Missing retros:\n{debt_lines}\n\n"
            f"Use leroy_update_spec(task_id, pass_rate, retrospective) to write each retro.\n"
            f'To override (emergency only): include "RETRO_OVERRIDE" in the spec subject.'
        )

    override_warning = (
        f"\nWARNING: Retro gate overridden. You still owe {len(retro_debt)} retrospective(s).\n"
        if override and retro_debt
        else ""
    )

    # ---------------------------------------------------------------------------
    # Layer 1: Persist gate -- block new specs if PM hasn't persisted to brain recently
    # ---------------------------------------------------------------------------
    if "PERSIST_OVERRIDE" not in subject.upper():
        persist_stale = False
        persist_warning = ""
        try:
            # Try server endpoint first (2s timeout to keep leroy_send_spec fast)
            import urllib.request as _urllib_request, json as _json_persist
            req = _urllib_request.Request("http://localhost:9801/persist/last?source=pm")
            with _urllib_request.urlopen(req, timeout=2) as resp:
                data = _json_persist.loads(resp.read())
                if data.get("stale", True):
                    age = data.get("age_seconds")
                    if age is not None:
                        hours = age // 3600
                        persist_stale = True
                        persist_warning = f"PM has not persisted to brain in {hours}+ hours."
                    else:
                        persist_stale = True
                        persist_warning = "PM has never persisted to brain in this server lifetime."
        except Exception:
            # Server down -- check local ledger fallback
            import os as _os, time as _time
            ledger = _os.path.expanduser("~/.forge/logs/persist-ledger.json")
            if _os.path.exists(ledger):
                mtime = _os.path.getmtime(ledger)
                age = _time.time() - mtime
                if age > 14400:
                    persist_stale = True
                    persist_warning = f"Local ledger stale ({int(age // 3600)}+ hours). Server unreachable."
                # else: ledger is fresh, fail open -- no block
            else:
                # Neither server nor ledger available -- fail open with loud warning
                persist_stale = False
                persist_warning = "CRITICAL: Cannot verify brain persist status. Persist context immediately."
                override_warning += f"\n{persist_warning}\n"

        if persist_stale:
            return (
                f"BLOCKED by persist gate: {persist_warning}\n\n"
                f"Persist your current context to brain (call mcp__aianna__persist_on or persist_append),\n"
                f"then resend the spec.\n"
                f'Add PERSIST_OVERRIDE to subject to bypass (emergency only).'
            )

    # ---------------------------------------------------------------------------
    # v2 Phase 2: Spec Analyzer pipeline
    # ---------------------------------------------------------------------------
    typed_ir = extract_typed_ir(spec, subject)
    analyzer_notes = []

    # Dedup check (fetch active/recent tasks for comparison)
    dedup_override = "DEDUP_OVERRIDE" in subject
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{_a2a_url()}/tasks", headers=_headers(),
                                     params={"status": "working"})
            active_tasks = resp.json().get("tasks", []) if resp.status_code == 200 else []
            resp = await client.get(f"{_a2a_url()}/tasks", headers=_headers(),
                                     params={"status": "completed", "limit": "20"})
            recent_tasks = resp.json().get("tasks", []) if resp.status_code == 200 else []
    except Exception:
        active_tasks, recent_tasks = [], []

    dedup = check_dedup(typed_ir, subject, active_tasks, recent_tasks)
    if dedup["blocked"] and not dedup_override:
        return f"BLOCKED: {dedup['message']}\nInclude DEDUP_OVERRIDE in subject to bypass."
    if dedup["message"]:
        analyzer_notes.append(dedup["message"])

    # Complexity check
    scope_override = "SCOPE_OVERRIDE" in subject
    complexity = check_complexity(typed_ir, spec)
    if complexity["warnings"]:
        warnings_str = "\n".join(f"  - {w}" for w in complexity["warnings"])
        analyzer_notes.append(f"Complexity warnings:\n{warnings_str}")

    # Pre-flight check
    preflight = check_preflight(typed_ir)
    if preflight["blocked"]:
        failed_checks = [c for c in preflight["checks"] if not c["up"]]
        checks_str = ", ".join(f"{c['name']} ({c['host']}:{c['port']})" for c in failed_checks)
        return f"BLOCKED: Pre-flight failed. Unreachable: {checks_str}\nFix infrastructure before sending."
    if preflight["checks"]:
        passed_str = ", ".join(c["name"] for c in preflight["checks"] if c["up"])
        if passed_str:
            analyzer_notes.append(f"Pre-flight passed: {passed_str}")

    # ---------------------------------------------------------------------------
    # v2 Phase 5A: Brain query (Gate 0) -- check_before_act
    # ---------------------------------------------------------------------------
    brain_queried = False
    brain_lessons_text = None
    brain_override = "BRAIN_OVERRIDE" in subject

    if not brain_override:
        brain_result = _brain.check_before_act(subject or spec[:200])
        brain_queried = brain_result.get("queried", False)

        if brain_queried:
            warnings = brain_result.get("warnings", [])
            lessons = brain_result.get("lessons", [])

            if warnings:
                warnings_str = "\n".join(f"  - {w}" for w in warnings[:5])
                analyzer_notes.append(f"Brain warnings:\n{warnings_str}")

            if lessons:
                # Format lessons for attachment to spec
                lesson_lines = []
                for lesson in lessons[:5]:
                    if isinstance(lesson, dict):
                        lesson_lines.append(f"- {lesson.get('content', lesson.get('lesson', str(lesson)))}")
                    else:
                        lesson_lines.append(f"- {lesson}")
                brain_lessons_text = "\n".join(lesson_lines)
                analyzer_notes.append(f"Brain lessons ({len(lessons)} found)")

            # Attach raw response for debugging
            raw = brain_result.get("raw", "")
            if raw and not warnings and not lessons:
                # check_before_act returned text, not structured data
                brain_lessons_text = raw[:500]
                analyzer_notes.append("Brain: check_before_act returned unstructured response")
        else:
            error = brain_result.get("error", "unknown")
            analyzer_notes.append(f"Brain: unavailable ({error}), proceeding without lessons")

    # ---------------------------------------------------------------------------
    # v2 Phase 3: Create plan record before sending
    # ---------------------------------------------------------------------------
    plan_id = None
    try:
        store = _get_plan_store()
        plan_id = store.create_plan(
            spec_text=spec,
            subject=subject or _derive_slug(subject),
            typed_ir=typed_ir.to_json(),
            complexity_score=typed_ir.complexity,
            criteria_count=len(typed_ir.criteria),
            target_machine=typed_ir.target,
            subsystem=typed_ir.subsystem,
            preflight_passed=preflight["passed"],
            preflight_details=json.dumps(preflight["checks"]) if preflight["checks"] else None,
            dedup_checked=True,
            dedup_similar_task_id=dedup.get("overlapping_task_id"),
            brain_queried=brain_queried,
            brain_lessons_attached=brain_lessons_text,
        )
    except Exception as e:
        plan_id = None  # Non-fatal: plan creation failed

    # v2 Phase 9: Pre-send quality scoring
    quality_breakdown = score_pre_send(
        typed_ir, spec,
        brain_queried=brain_queried,
        dedup_result=dedup,
        preflight_result=preflight,
        complexity_result=complexity,
    )
    pre_send_quality = quality_breakdown.total_score

    # Store pre-send score on plan record
    if plan_id:
        try:
            store = _get_plan_store()
            store.update_outcome(plan_id, quality_score=pre_send_quality)
        except Exception:
            pass  # Non-fatal

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

    # v2 Phase 5A: Append brain lessons to spec if available
    spec_with_lessons = spec
    if brain_lessons_text:
        spec_with_lessons = spec + f"\n\n## Prior Lessons (from Aianna brain)\n{brain_lessons_text}\n"

    # Read 10 most recent specs BEFORE saving this one
    recent_files = _get_recent_spec_files(10)
    recent_summary = _format_recent_specs_summary(recent_files)

    # Save spec to disk (with lessons appended)
    spec_path.write_text(front_matter + spec_with_lessons, encoding="utf-8")

    # Send to A2A (send spec with lessons so builder has context)
    message_id = uuid4().hex
    payload = {
        "jsonrpc": "2.0",
        "id": message_id,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": spec_with_lessons}],
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

    # v2 Phase 3: Link plan to task
    if plan_id and task_id:
        try:
            _get_plan_store().link_task(plan_id, task_id)
        except Exception:
            pass  # Non-fatal

    # Update saved spec file with real task_id
    try:
        content = spec_path.read_text(encoding="utf-8")
        updated = _update_frontmatter(content, {"task_id": task_id})
        spec_path.write_text(updated, encoding="utf-8")
    except Exception as e:
        # Non-fatal: spec is saved, task_id update failed
        pass

    # v2: store typed IR in task metadata via REST
    try:
        import json
        async with httpx.AsyncClient(timeout=10) as client:
            await client.patch(
                f"{_a2a_url()}/tasks/{task_id}",
                headers=_headers(),
                json={"typed_ir": typed_ir.to_json()},
            )
    except Exception:
        pass  # Non-fatal

    subject_line = f" ({subject})" if subject else ""
    analyzer_summary = ""
    if analyzer_notes:
        analyzer_summary = "\n\nAnalyzer notes:\n" + "\n".join(f"  {n}" for n in analyzer_notes)
    ir_summary = (
        f"\nTyped IR: {len(typed_ir.criteria)} criteria, "
        f"target={typed_ir.target or 'local'}, "
        f"subsystem={typed_ir.subsystem or 'unknown'}, "
        f"complexity={typed_ir.complexity}"
    )
    brain_summary = f"\nBrain: {'queried' if brain_queried else 'skipped'}"
    if brain_lessons_text:
        brain_summary += f", {len(brain_lessons_text)} chars of lessons attached"
    quality_summary = f"\nQuality score: {pre_send_quality:.2f} (pre-send)"
    return (
        f"Spec sent to Leroy{subject_line}. Task ID: {task_id}\n"
        f"Spec saved: {spec_path.name}\n"
        f"Leroy is working on it. Check progress with: leroy_check_task('{task_id}')\n"
        f"{ir_summary}{brain_summary}{quality_summary}{analyzer_summary}"
        f"{override_warning}"
        f"\n{recent_summary}"
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

    # v2 Phase 3: Update plan record with outcome
    try:
        store = _get_plan_store()
        plan = store.get_plan_by_task(task_id)
        if plan:
            store.update_outcome(
                plan["plan_id"],
                status=new_status,
                pass_rate=pass_rate,
                retro_text=retrospective,
                outcome="verified" if new_status == "completed" else "failed",
            )
    except Exception:
        pass  # Non-fatal

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
            f"  [{t['status'].upper():>10}] {t['task_id']} "
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
    endpoint = "/messages?to=pm&pending=true" if pending_only else "/messages?to=pm"
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
        sender = msg.get("from", "unknown")
        content = msg.get("content", "")
        context = msg.get("context", "")
        requires_response = msg.get("requires_response", False)
        responded = msg.get("responded", False)
        ts = msg.get("created_at", "")[:19].replace("T", " ")

        lines.append(f"[{msg_type}] message_id: {msg_id}")
        lines.append(f"  From: {sender}")
        lines.append(f"  Task: {task_id}")
        lines.append(f"  Time: {ts}")
        lines.append(f"  Content: {content}")
        if context:
            lines.append(f"  Context: {context}")
        if requires_response and not responded:
            lines.append(f"  ** AWAITING YOUR RESPONSE -- use leroy_reply_to_message('{msg_id}', ...) **")
        elif responded:
            lines.append(f"  [RESPONDED: {msg.get('response', '')}]")
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
        "from": "pm",
        "content": response,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(
                f"{_a2a_url()}/messages/{message_id}/respond",
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
    msgs = data.get("messages", {})
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

    if msgs:
        lines.append(f"Messages: {msgs.get('total_pending', 0)} total pending")
        agents = msgs.get("agents", {})
        for name, counts in agents.items():
            lines.append(
                f"  [{name}]: {counts.get('unread', 0)} unread, "
                f"{counts.get('pending', 0)} awaiting response"
            )

    if persist:
        lines.append(
            f"Brain: queue_depth={persist.get('queue_depth', '?')}"
        )

    return "\n".join(lines)


@mcp.tool()
async def leroy_send_message(to: str, content: str, msg_type: str = "request",
                              task_id: str = "") -> str:
    """Send a message to any agent on the bus.

    Use this to communicate with ops, leroy, content-agent, or any registered agent.
    Messages are delivered to the recipient's inbox on the agent message bus.

    Args:
        to: Recipient agent name (e.g. "ops", "leroy", "content-agent").
        content: The message text.
        msg_type: Message type: "request", "question", "status_update", "alert".
                  Default "request".
        task_id: Optional task ID to link the message to.

    Returns:
        Confirmation with message ID.
    """
    payload = {
        "from": "pm",
        "to": to,
        "type": msg_type,
        "content": content,
    }
    if task_id:
        payload["task_id"] = task_id

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(
                f"{_a2a_url()}/messages",
                headers=_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.ConnectError:
            return "Cannot reach Leroy A2A server. Is it running on port 9800?"
        except Exception as e:
            return f"Error sending message: {e}"

    msg_id = data.get("message_id", "unknown")
    return (
        f"Message sent to {to}.\n"
        f"Message ID: {msg_id}\n"
        f"Type: {msg_type}\n"
        f"Content: {content[:100]}"
    )


# ---------------------------------------------------------------------------
# v2 Phase 3: Plan Database MCP Tools
# ---------------------------------------------------------------------------
@mcp.tool()
async def leroy_list_plans(status: str = "", since_date: str = "",
                           limit: int = 50, subsystem: str = "",
                           source: str = "") -> str:
    """List plans from the plan database.

    By default excludes v1 imports. Set source='v1_import' to see them.

    Args:
        status: Filter by status (draft, sent, completed, failed).
        since_date: ISO date to filter from (e.g. '2026-03-01').
        limit: Max results (default 50).
        subsystem: Filter by subsystem (dashboard, server, mcp, monitor).
        source: Filter by source ('v2' or 'v1_import'). Empty excludes v1.

    Returns:
        Formatted plan list.
    """
    try:
        store = _get_plan_store()
        plans = store.list_plans(
            status=status or None,
            since_date=since_date or None,
            limit=limit,
            subsystem=subsystem or None,
            source=source or None,
        )
    except Exception as e:
        return f"Error listing plans: {e}"

    if not plans:
        return "No plans found matching filters."

    lines = [f"Plans ({len(plans)}):"]
    for p in plans:
        subject = p.get("subject", "untitled")[:50]
        s = p.get("status", "?")
        src = p.get("source", "v2")
        sub = p.get("subsystem") or "?"
        created = (p.get("created_at") or "")[:10]
        pr = p.get("pass_rate") or ""
        brain = "B" if p.get("brain_queried") else "-"
        lines.append(f"  {created} | {p['plan_id']} | {s:<10} | {sub:<12} | {src} | {brain} | {subject} | {pr}")
    return "\n".join(lines)


@mcp.tool()
async def leroy_plan_report() -> str:
    """Get aggregate plan statistics with separate v1/v2 baselines.

    Returns:
        Plan report with completion rates, costs, timeout rates, and respec counts.
    """
    try:
        store = _get_plan_store()
        report = store.plan_report()
    except Exception as e:
        return f"Error generating plan report: {e}"

    lines = ["Plan Report:"]
    for label, stats in [("v2", report["v2"]), ("v1_import", report["v1_import"]), ("Combined", report["combined"])]:
        if stats["total"] == 0:
            lines.append(f"\n  {label}: No plans")
            continue
        lines.append(f"\n  {label}:")
        lines.append(f"    Total: {stats['total']}")
        lines.append(f"    Completed: {stats.get('completed', 0)}")
        lines.append(f"    Failed: {stats.get('failed', 0)}")
        lines.append(f"    Total cost: ${stats.get('total_cost_usd', 0):.4f}")
        lines.append(f"    Avg cost: ${stats.get('avg_cost_usd', 0):.4f}")
        lines.append(f"    Respec count: {stats.get('respec_count', 0)}")
        lines.append(f"    Timeouts: {stats.get('timeout_count', 0)}")
        lines.append(f"    Brain queried: {stats.get('brain_queried', 0)}")
        lines.append(f"    Brain persisted: {stats.get('brain_persisted', 0)}")
    return "\n".join(lines)


@mcp.tool()
async def leroy_brain_gaps() -> str:
    """Find plans where brain was not queried or results not persisted.

    Returns:
        List of non-compliant plans.
    """
    try:
        store = _get_plan_store()
        gaps = store.brain_gaps()
    except Exception as e:
        return f"Error checking brain gaps: {e}"

    if not gaps:
        return "No brain gaps found. All v2 plans are brain-compliant."

    lines = [f"Brain gaps ({len(gaps)} plans):"]
    for g in gaps:
        queried = "queried" if g.get("brain_queried") else "NOT queried"
        persisted = "persisted" if g.get("brain_persisted") else "NOT persisted"
        lines.append(f"  {g['plan_id']} | {g.get('subject', '?')[:40]} | {queried} | {persisted}")
    return "\n".join(lines)


@mcp.tool()
async def leroy_cost_report(since_date: str = "") -> str:
    """Get token usage and cost breakdown by subsystem and day.

    Args:
        since_date: ISO date to filter from (e.g. '2026-03-01'). Empty = all time.

    Returns:
        Cost report with per-subsystem and per-day breakdowns.
    """
    try:
        store = _get_plan_store()
        report = store.cost_report(since_date=since_date or None)
    except Exception as e:
        return f"Error generating cost report: {e}"

    lines = [f"Cost Report (total: ${report['total_cost_usd']:.4f}):"]

    if report["by_subsystem"]:
        lines.append("\n  By subsystem:")
        for sub, stats in report["by_subsystem"].items():
            lines.append(f"    {sub}: ${stats['cost']:.4f} ({stats['count']} plans, "
                        f"{stats['input_tokens']} in / {stats['output_tokens']} out)")

    if report["by_day"]:
        lines.append("\n  By day:")
        for day, stats in report["by_day"].items():
            lines.append(f"    {day}: ${stats['cost']:.4f} ({stats['count']} plans)")

    if not report["by_subsystem"] and not report["by_day"]:
        lines.append("  No cost data recorded yet.")

    return "\n".join(lines)


@mcp.tool()
async def leroy_subsystem_health() -> str:
    """Get per-subsystem pass rate and respec count.

    Returns:
        Health stats for each subsystem.
    """
    try:
        store = _get_plan_store()
        health = store.subsystem_health()
    except Exception as e:
        return f"Error generating subsystem health: {e}"

    if not health:
        return "No v2 plans recorded yet."

    lines = ["Subsystem Health:"]
    for sub, stats in sorted(health.items()):
        lines.append(f"  {sub}:")
        lines.append(f"    Total: {stats['total']}, Completed: {stats['completed']}, "
                    f"Failed: {stats['failed']}")
        lines.append(f"    Pass rate: {stats['pass_rate']:.0%}")
        lines.append(f"    Respec count: {stats['respec_count']}")
    return "\n".join(lines)


@mcp.tool()
async def leroy_tail_task(task_id: str, lines: int = 50) -> str:
    """Tail a running task's log with live observability fields.

    Single HTTP call to GET /tasks/{task_id}/logs?tail={lines}.
    Returns status, log lines, PID info, elapsed time, CPU %, target, and
    container info in a human-readable format.

    Args:
        task_id: The task ID to inspect.
        lines: Number of log tail lines to return (default 50).

    Returns:
        Formatted observability output, or error if task/log not found.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(
                f"{_a2a_url()}/tasks/{task_id}/logs",
                headers=_headers(),
                params={"tail": str(lines)},
            )
        except httpx.ConnectError:
            return "Cannot reach Leroy A2A server. Is it running on port 9800?"
        except Exception as e:
            return f"Error fetching task logs: {e}"

    data = resp.json()

    # Handle errors
    if resp.status_code == 404:
        error = data.get("error", "task or log not found")
        # Check if it's a missing log vs missing task
        if "no log file" in error:
            # Task exists but no log yet -- show what we have
            status = data.get("status", "unknown")
            elapsed = data.get("elapsed_seconds")
            target = data.get("target", "haze")
            is_container = data.get("is_container", False)
            vehicle_ids = data.get("vehicle_ids") or []

            out = [
                f"Task: {task_id}",
                f"Status: {status}",
                f"Target: {target}",
                f"Elapsed: {elapsed:.0f}s" if elapsed is not None else "Elapsed: unknown",
                "",
                "No log file yet (task may be queued or pre-execution).",
            ]
            if is_container:
                out.append(f"Container task. Vehicle IDs: {', '.join(vehicle_ids)}")
                out.append("Tail individual vehicles for log output.")
            return "\n".join(out)
        return f"Task {task_id} not found."

    if resp.status_code != 200:
        return f"Error {resp.status_code}: {data.get('error', 'unknown error')}"

    # --- Format response ---
    status = data.get("status", "unknown")
    elapsed = data.get("elapsed_seconds")
    cpu = data.get("cpu_percent")
    target = data.get("target", "haze")
    remote_hint = data.get("remote_log_hint")
    is_container = data.get("is_container", False)
    vehicle_ids = data.get("vehicle_ids") or []
    process = data.get("process") or {}
    log_lines = data.get("log_lines", [])
    total_lines = data.get("total_lines", 0)
    showing = data.get("showing", 0)
    last_activity = data.get("last_activity", "")
    stuck = data.get("stuck_detected")

    elapsed_str = f"{elapsed:.0f}s" if elapsed is not None else "unknown"
    cpu_str = f"{cpu:.1f}%" if cpu is not None else "n/a"

    pid = process.get("pid")
    pid_alive = process.get("alive", False)
    pid_str = f"PID {pid} ({'alive' if pid_alive else 'dead'})" if pid else "no active process"

    header = [
        f"Task:     {task_id}",
        f"Status:   {status}",
        f"Target:   {target}",
        f"Elapsed:  {elapsed_str}",
        f"CPU:      {cpu_str}",
        f"Process:  {pid_str}",
    ]
    if last_activity:
        header.append(f"Last act: {last_activity[:19].replace('T', ' ')}")
    if stuck:
        header.append(f"STUCK AT: {stuck}")
    if is_container:
        header.append(f"Container: YES | Vehicles: {', '.join(vehicle_ids) or 'none'}")
    if remote_hint:
        header.append(f"Note: {remote_hint}")
    if data.get("error"):
        header.append(f"Error: {data['error']}")

    header.append(f"\n--- Log ({showing}/{total_lines} lines) ---")

    if log_lines:
        log_block = "\n".join(log_lines)
    else:
        log_block = "(no log output)"

    return "\n".join(header) + "\n" + log_block


if __name__ == "__main__":
    mcp.run(transport="stdio")
