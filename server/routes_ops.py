"""Ops, analytics, and infrastructure route handlers.

Extracted from server.py -- handles agents, activity, proposals, ideas, specs,
plans, validation, improvement, quality, queue, webhooks, PM autonomy,
knowledge governance, brain health, and infrastructure status.
"""
import asyncio
import json
import re
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

import agent_bus
import config
import task_db
import server_state as state
from state_machine import TaskState
from execution import WORK_DIR
from knowledge_governance import governance_metrics as kg_metrics, prune_stale_knowledge
from pm_autonomy import (
    classify_decision, evaluate_autonomy,
    get_confidence_map, should_auto_execute,
)
from task_analytics import (
    score_post_outcome, quality_metrics as qm_metrics,
    validate_criteria, detect_hallucination, detect_drift,
    make_verification_decision, ValidationResult,
)
from improvement_engine import (
    analyze_patterns, learn_thresholds, find_golden_templates,
    generate_suggestions, baseline_comparison, full_analysis,
)


# ---------------------------------------------------------------------------
# Agent registry endpoints
# ---------------------------------------------------------------------------

# Known agents seeded at startup (Phase 1: static roster)
_SEED_AGENTS = [
    {
        "name": "pm",
        "display_name": "PM",
        "type": "interactive",
        "launcher": "pm.sh",
        "status": "idle",
        "current_task": None,
        "last_heartbeat": None,
        "last_activity": None,
        "metadata": {
            "launch_method": "manual",
            "description": "Product Manager -- specs, decisions, delegation",
        },
    },
    {
        "name": "leroy",
        "display_name": "Leroy",
        "type": "daemon",
        "launcher": "leroy.sh",
        "status": "idle",
        "current_task": None,
        "last_heartbeat": None,
        "last_activity": None,
        "metadata": {
            "launch_method": "launchd",
            "description": "Engineering Lead -- executes specs via claude CLI",
        },
    },
    {
        "name": "ops",
        "display_name": "Ops",
        "type": "on-demand",
        "launcher": "ops.sh",
        "status": "idle",
        "current_task": None,
        "last_heartbeat": None,
        "last_activity": None,
        "metadata": {
            "launch_method": "manual",
            "description": "Infrastructure ops and troubleshooting",
        },
    },
    {
        "name": "content-agent",
        "display_name": "Content Agent",
        "type": "scheduled",
        "launcher": "content.sh",
        "status": "idle",
        "current_task": None,
        "last_heartbeat": None,
        "last_activity": None,
        "metadata": {
            "launch_method": "launchd",
            "schedule": "daily 6AM CST",
            "description": "Daily content pipeline -- queries Aianna, generates drafts",
        },
    },
]

_HEARTBEAT_WINDOW_SECONDS = 60  # seconds per heartbeat window
_HEARTBEAT_MISS_THRESHOLD = 3  # consecutive missed windows = unreachable


async def agents_list(request: Request) -> JSONResponse:
    """GET /agents -- Returns registered agent roster with status fields."""
    agents = state._agent_store.list_all()
    # Compute unreachable status based on last_heartbeat
    now = datetime.now(timezone.utc)
    for agent in agents:
        lhb = agent.get("last_heartbeat")
        if lhb and agent.get("status") not in ("error",):
            try:
                lhb_dt = datetime.fromisoformat(lhb)
                elapsed = (now - lhb_dt).total_seconds()
                if elapsed > _HEARTBEAT_WINDOW_SECONDS * _HEARTBEAT_MISS_THRESHOLD:
                    agent["status"] = "unreachable"
            except Exception:
                pass
    return JSONResponse({"agents": agents, "count": len(agents)})


async def agent_heartbeat(request: Request) -> JSONResponse:
    """POST /agents/{name}/heartbeat -- Agent reports status and current task.

    Body: {"status": "idle|running|error", "current_task": null|"task_id", "metadata": {}}
    """
    name = request.path_params["name"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    now = datetime.now(timezone.utc).isoformat()
    existing = state._agent_store.get(name)
    if existing is None:
        # Auto-register unknown agents
        existing = {
            "name": name,
            "display_name": name.replace("-", " ").title(),
            "type": "on-demand",
            "launcher": "unknown",
            "status": "idle",
            "current_task": None,
            "last_heartbeat": None,
            "last_activity": None,
            "metadata": {},
        }

    existing["last_heartbeat"] = now
    existing["last_activity"] = now
    if "status" in body:
        existing["status"] = body["status"]
    if "current_task" in body:
        existing["current_task"] = body.get("current_task")
    if "metadata" in body and isinstance(body["metadata"], dict):
        if "metadata" not in existing or not isinstance(existing.get("metadata"), dict):
            existing["metadata"] = {}
        existing["metadata"].update(body["metadata"])

    # Allow heartbeat to update display_name, type, launcher if provided
    for field in ("display_name", "type", "launcher"):
        if field in body:
            existing[field] = body[field]

    state._agent_store.upsert(existing)
    return JSONResponse({"status": "ok", "name": name, "updated_at": now})


async def agent_delete(request: Request) -> JSONResponse:
    """DELETE /agents/{name} -- Remove an agent from the roster."""
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    name = request.path_params["name"]
    if name not in state._agent_store:
        return JSONResponse({"error": f"agent {name} not found"}, status_code=404)

    with state._agent_store._lock:
        state._agent_store._agents.pop(name, None)
    with state._agent_store._db._write_lock:
        state._agent_store._db._conn.execute("DELETE FROM agents WHERE name = ?", (name,))
        state._agent_store._db._conn.commit()
    state.logger.info("Agent %s deleted from roster", name)
    return JSONResponse({"status": "ok", "name": name, "deleted": True})


# ---------------------------------------------------------------------------
# Activity feed endpoints
# ---------------------------------------------------------------------------

async def activity_create(request: Request) -> JSONResponse:
    """POST /activity -- Create an activity event from an external agent/monitor.

    Body: {agent, type, summary, severity?, task_id?, detail?}
    No auth -- localhost only (monitors and sidecars).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    agent = body.get("agent")
    event_type = body.get("type")
    summary = body.get("summary")
    if not all([agent, event_type, summary]):
        return JSONResponse({"error": "agent, type, and summary required"}, status_code=400)

    state._emit_activity(
        agent, event_type, summary,
        detail=body.get("detail"),
        task_id=body.get("task_id"),
        severity=body.get("severity", "info"),
    )
    return JSONResponse({"status": "ok"})


async def activity_list(request: Request) -> JSONResponse:
    """GET /activity -- Returns recent activity events.

    Query params:
      ?limit=50   (default 100, max 500)
      ?since=<iso8601>
      ?agent=<name>
    """
    limit = min(int(request.query_params.get("limit", "100")), 500)
    since = request.query_params.get("since")
    agent_filter = request.query_params.get("agent")
    events = state._activity_store.list_recent(limit=limit, since=since, agent=agent_filter)
    return JSONResponse({"events": events, "count": len(events)})


async def activity_stream(request: Request) -> StreamingResponse:
    """GET /activity/stream -- SSE stream of activity events.

    Sends:
    - Recent events snapshot on connect
    - New events as they are emitted
    - Heartbeat every 15 seconds
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    state._activity_sse_subscribers.add(queue)

    # Wire activity store to push into this queue
    def _push(evt):
        try:
            queue.put_nowait(json.dumps({"type": "activity_event", "event": evt}))
        except asyncio.QueueFull:
            state._activity_sse_subscribers.discard(queue)

    state._activity_store.add_sse_subscriber(_push)

    async def event_generator():
        try:
            # Send recent snapshot
            snapshot_events = state._activity_store.list_recent(limit=50)
            snapshot = json.dumps({"type": "activity_snapshot", "events": snapshot_events})
            yield f"data: {snapshot}\n\n"

            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    heartbeat = json.dumps({
                        "type": "heartbeat",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    yield f"data: {heartbeat}\n\n"
        except Exception:
            pass
        finally:
            state._activity_sse_subscribers.discard(queue)
            state._activity_store.remove_sse_subscriber(_push)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# PM Proposal approval queue endpoints
# ---------------------------------------------------------------------------

async def proposals_create(request: Request) -> JSONResponse:
    """POST /pm/proposals -- Headless PM submits a draft spec for Brad's approval.

    Body: {proposal_type, title, content, reasoning, trigger_event?, trigger_task_id?}
    No auth -- localhost only.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    title = body.get("title")
    content = body.get("content")
    if not title or not content:
        return JSONResponse({"error": "title and content required"}, status_code=400)

    from uuid import uuid4
    proposal = {
        "proposal_id": uuid4().hex,
        "status": "pending",
        "proposal_type": body.get("proposal_type", "build_spec"),
        "trigger_event": body.get("trigger_event"),
        "trigger_task_id": body.get("trigger_task_id"),
        "title": title,
        "content": content,
        "reasoning": body.get("reasoning", ""),
    }
    stored = state._proposal_store.create(proposal)

    state._emit_activity("pm-headless", "proposal_created",
                   f"New proposal: {title}",
                   task_id=body.get("trigger_task_id"),
                   severity="warn")

    state.logger.info("Proposal created: %s -- %s", stored["proposal_id"], title)
    return JSONResponse({"proposal_id": stored["proposal_id"], "status": "pending"})


async def proposals_list(request: Request) -> JSONResponse:
    """GET /pm/proposals -- List proposals, optionally filtered by status.

    Query params: ?status=pending (default), ?limit=50
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    status = request.query_params.get("status", "pending")
    limit = int(request.query_params.get("limit", "50"))

    if status == "all":
        proposals = state._proposal_store.list_all(limit=limit)
    else:
        proposals = state._proposal_store.list_by_status(status=status, limit=limit)

    return JSONResponse({"proposals": proposals, "count": len(proposals)})


async def proposals_approve(request: Request) -> JSONResponse:
    """POST /pm/proposals/{proposal_id}/approve -- Brad approves a proposal.

    Body: {feedback?: "optional note"}
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    proposal_id = request.path_params["proposal_id"]
    proposal = state._proposal_store.get(proposal_id)
    if proposal is None:
        return JSONResponse({"error": f"proposal {proposal_id} not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        body = {}

    now = datetime.now(timezone.utc).isoformat()
    updated = state._proposal_store.update(proposal_id, {
        "status": "approved",
        "reviewed_at": now,
        "reviewer_feedback": body.get("feedback"),
    })

    # Notify on bus so monitor can spawn headless PM to execute
    agent_bus.send({
        "from": "brad",
        "to": "pm-headless",
        "type": "approval",
        "content": f"Proposal approved: {proposal.get('title', '')}",
        "task_id": proposal.get("trigger_task_id"),
        "context": json.dumps({"proposal_id": proposal_id}),
    })

    state._emit_activity("brad", "proposal_approved",
                   f"Approved: {proposal.get('title', '')}",
                   task_id=proposal.get("trigger_task_id"))

    state.logger.info("Proposal %s approved", proposal_id)
    return JSONResponse({"status": "approved", "proposal_id": proposal_id})


async def proposals_reject(request: Request) -> JSONResponse:
    """POST /pm/proposals/{proposal_id}/reject -- Brad rejects a proposal.

    Body: {feedback: "why it was rejected"}
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    proposal_id = request.path_params["proposal_id"]
    proposal = state._proposal_store.get(proposal_id)
    if proposal is None:
        return JSONResponse({"error": f"proposal {proposal_id} not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        body = {}

    feedback = body.get("feedback", "")
    now = datetime.now(timezone.utc).isoformat()
    state._proposal_store.update(proposal_id, {
        "status": "rejected",
        "reviewed_at": now,
        "reviewer_feedback": feedback,
    })

    state._emit_activity("brad", "proposal_rejected",
                   f"Rejected: {proposal.get('title', '')}",
                   detail=feedback,
                   task_id=proposal.get("trigger_task_id"))

    state.logger.info("Proposal %s rejected: %s", proposal_id, feedback)
    return JSONResponse({"status": "rejected", "proposal_id": proposal_id, "feedback": feedback})


# ---------------------------------------------------------------------------
# Ideas endpoints
# ---------------------------------------------------------------------------

async def ideas_create(request: Request) -> JSONResponse:
    """POST /ideas -- Create an idea task (lightweight backlog placeholder).

    Body: {"title": "Short idea title", "description": "Optional one-liner"}
    Returns: created task with status "idea" and task_id.
    Ideas do NOT trigger auto-execution.
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    title = (body.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "title required"}, status_code=400)

    description = (body.get("description") or "").strip()
    task_id = uuid4().hex
    now = datetime.now(timezone.utc).isoformat()

    task = {
        "task_id": task_id,
        "spec": title,
        "description": description,
        "status": "idea",
        "result": None,
        "created_at": now,
        "completed_at": None,
    }
    state._task_meta[task_id] = task
    state._broadcast_task_update_sync(task_id)

    state._emit_activity("pm", "idea_created", f"Idea created: {title[:80]}", task_id=task_id)
    state.logger.info("Idea created: %s -- %s", task_id, title[:60])

    return JSONResponse(dict(state._task_meta[task_id]), status_code=201)


async def ideas_promote(request: Request) -> JSONResponse:
    """POST /ideas/{task_id}/promote -- Promote an idea to pending.

    Changes status from "idea" to "pending".
    Optional body: {"spec": "# Full spec markdown..."} to replace the placeholder spec.
    If no spec body, the idea title becomes the spec.
    Does NOT trigger auto-execution -- task sits in pending until picked up by Leroy CLI.
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    if task_id not in state._task_meta:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)

    if state._task_meta[task_id]["status"] != "idea":
        return JSONResponse(
            {"error": f"task {task_id} cannot be promoted (status: {state._task_meta[task_id]['status']})"},
            status_code=409,
        )

    try:
        body = await request.json()
    except Exception:
        body = {}

    # If a spec is provided, replace the placeholder
    if body.get("spec"):
        state._task_meta[task_id]["spec"] = body["spec"]

    state._task_meta[task_id]["status"] = "pending"
    state._task_meta[task_id]["promoted_at"] = datetime.now(timezone.utc).isoformat()

    state.logger.info("Idea %s promoted to pending", task_id)
    state._broadcast_task_update_sync(task_id)
    state._emit_activity("pm", "idea_promoted",
                   f"Idea promoted to pending: {state._task_meta[task_id]['spec'][:60]}",
                   task_id=task_id)

    return JSONResponse({
        "status": "ok",
        "task_id": task_id,
        "new_status": "pending",
        "task": dict(state._task_meta[task_id]),
    })


# ---------------------------------------------------------------------------
# Specs pipeline endpoint
# ---------------------------------------------------------------------------

async def specs_list(request: Request) -> JSONResponse:
    """GET /specs -- Returns specs with pipeline stage derived from task metadata.

    Pipeline stages:
      draft   -- specs in ~/Projects/leroy/specs/drafts/ not yet sent
      sent    -- task status == pending
      building -- task status == working | waiting_for_pm
      qa      -- task status == qa_review
      done    -- task status == completed
      failed  -- task status == failed | cancelled
    """
    import re

    def _extract_title(spec_text: str) -> str:
        if not spec_text:
            return "Untitled"
        for line in spec_text.splitlines():
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()
            if line.startswith("Subject:"):
                return line[8:].strip()
        # Fall back to first non-empty line
        for line in spec_text.splitlines():
            if line.strip():
                return line.strip()[:80]
        return "Untitled"

    def _task_to_stage(status: str) -> str:
        if status in ("pending",):
            return "sent"
        elif status in ("working", "waiting_for_pm"):
            return "building"
        elif status == "qa_review":
            return "qa"
        elif status == "completed":
            return "done"
        elif status in ("failed", "cancelled"):
            return "failed"
        return "sent"

    specs = []
    for task in state._task_meta.values():
        stage = _task_to_stage(task.get("status", "pending"))
        title = _extract_title(task.get("spec", ""))
        # Detect QA tasks by title convention
        is_qa_task = bool(re.search(r'\bqa\b|\bquality assurance\b', title, re.IGNORECASE))
        created_at = task.get("created_at", "")
        completed_at = task.get("completed_at", "")

        # Calculate time in stage
        reference_time = completed_at if completed_at else created_at
        time_in_stage_s = None
        if reference_time:
            try:
                ref_dt = datetime.fromisoformat(reference_time)
                time_in_stage_s = int((datetime.now(timezone.utc) - ref_dt).total_seconds())
            except Exception:
                pass

        qa_pass_rate = None
        if task.get("result"):
            # Extract QA pass rate from result string if present
            m = re.search(r'(\d+/\d+)\s*(?:pass|QA)', task["result"], re.IGNORECASE)
            if m:
                qa_pass_rate = m.group(1)

        specs.append({
            "task_id": task["task_id"],
            "title": title,
            "stage": stage,
            "is_qa_task": is_qa_task,
            "created_at": created_at,
            "completed_at": completed_at,
            "time_in_stage_seconds": time_in_stage_s,
            "qa_pass_rate": qa_pass_rate,
            "archived": task.get("archived", False),
        })

    # Sort: newest first
    specs.sort(key=lambda s: s["created_at"] or "", reverse=True)

    # Optionally include draft specs from filesystem
    draft_dir = Path(WORK_DIR) / "specs" / "drafts"
    if draft_dir.exists():
        for f in sorted(draft_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            try:
                content = f.read_text()
                title = _extract_title(content)
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()
                specs.insert(0, {
                    "task_id": None,
                    "title": title,
                    "stage": "draft",
                    "is_qa_task": False,
                    "created_at": mtime,
                    "completed_at": None,
                    "time_in_stage_seconds": None,
                    "qa_pass_rate": None,
                    "archived": False,
                    "draft_file": f.name,
                })
            except Exception:
                pass

    return JSONResponse({"specs": specs, "count": len(specs)})


# ---------------------------------------------------------------------------
# Plan endpoints (v2 Phase 3)
# ---------------------------------------------------------------------------
async def plans_list(request: Request) -> JSONResponse:
    """GET /plans -- List plans with optional filters."""
    store = task_db.plan_store
    if store is None:
        return JSONResponse({"plans": [], "count": 0})
    status = request.query_params.get("status")
    since_date = request.query_params.get("since_date")
    subsystem = request.query_params.get("subsystem")
    source = request.query_params.get("source")
    limit = int(request.query_params.get("limit", "50"))
    plans = store.list_plans(status=status, since_date=since_date,
                             subsystem=subsystem, source=source, limit=limit)
    return JSONResponse({"plans": plans, "count": len(plans)})


async def plans_detail(request: Request) -> JSONResponse:
    """GET /plans/{plan_id} -- Get a single plan."""
    store = task_db.plan_store
    if store is None:
        return JSONResponse({"error": "plan store not initialized"}, status_code=500)
    plan_id = request.path_params["plan_id"]
    plan = store.get_plan(plan_id)
    if plan is None:
        return JSONResponse({"error": f"plan {plan_id} not found"}, status_code=404)
    return JSONResponse(plan)


async def plans_report(request: Request) -> JSONResponse:
    """GET /plans/report -- Aggregate plan statistics."""
    store = task_db.plan_store
    if store is None:
        return JSONResponse({"v2": {"total": 0}, "v1_import": {"total": 0}, "combined": {"total": 0}})
    return JSONResponse(store.plan_report())


async def plans_cost(request: Request) -> JSONResponse:
    """GET /plans/cost -- Cost report by subsystem and day."""
    store = task_db.plan_store
    if store is None:
        return JSONResponse({"total_cost_usd": 0, "by_subsystem": {}, "by_day": {}})
    since_date = request.query_params.get("since_date")
    return JSONResponse(store.cost_report(since_date=since_date))


async def plans_subsystem_health(request: Request) -> JSONResponse:
    """GET /plans/subsystem-health -- Per-subsystem pass rate."""
    store = task_db.plan_store
    if store is None:
        return JSONResponse({})
    return JSONResponse(store.subsystem_health())


async def plans_brain_gaps(request: Request) -> JSONResponse:
    """GET /plans/brain-gaps -- Plans where brain not queried/persisted."""
    store = task_db.plan_store
    if store is None:
        return JSONResponse({"gaps": [], "count": 0})
    gaps = store.brain_gaps()
    return JSONResponse({"gaps": gaps, "count": len(gaps)})


# ---------------------------------------------------------------------------
# Criteria Validation endpoints (v2 Phase 11)
# ---------------------------------------------------------------------------

async def validate_task_criteria(request: Request) -> JSONResponse:
    """POST /validate/{task_id} -- Validate builder output against spec criteria.

    Optional body: {builder_claimed_pass: true}
    Runs criteria validation, hallucination detection, and returns recommendation.
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)

    task_id = request.path_params["task_id"]
    try:
        body = await request.json()
    except Exception:
        body = {}

    # Get plan and task metadata
    store = task_db.plan_store
    plan = store.get_plan_by_task(task_id) if store else None
    if not plan:
        return JSONResponse({"error": f"no plan found for task {task_id}"}, status_code=404)

    meta = state._task_meta.get(task_id) or {}
    typed_ir = plan.get("typed_ir")
    if typed_ir and isinstance(typed_ir, str):
        try:
            typed_ir = json.loads(typed_ir)
        except Exception:
            typed_ir = {}
    typed_ir = typed_ir or {}

    builder_sections = meta.get("builder_sections", {})
    result_text = meta.get("result", "") or meta.get("partial_result", "") or ""

    # Run validation
    validation = validate_criteria(
        typed_ir, builder_sections, result_text,
        task_id=task_id, plan_id=plan.get("plan_id", ""),
    )

    # Hallucination check
    builder_claimed = body.get("builder_claimed_pass", False)
    pass_rate = plan.get("pass_rate")
    validation = detect_hallucination(validation, builder_claimed, pass_rate)

    # Make decision
    decision = make_verification_decision(validation, result_text=result_text)
    validation.recommendation = decision

    # Execute state transition if applicable
    transition_result = None
    if state._state_machine and decision == "promote":
        try:
            state._state_machine.transition(task_id, TaskState.COMPLETED_VERIFIED,
                                       reason=f"criteria validation: {validation.verification_rate:.0%} verified")
            transition_result = "promoted to COMPLETED_VERIFIED"
        except Exception as e:
            transition_result = f"transition failed: {e}"
    elif state._state_machine and decision == "fail":
        try:
            state._state_machine.transition(task_id, TaskState.FAILED_RETRYABLE,
                                       reason=f"criteria validation: {validation.hallucination_reason or 'low verification rate'}")
            transition_result = "demoted to FAILED_RETRYABLE"
        except Exception as e:
            transition_result = f"transition failed: {e}"

    resp = validation.to_json()
    resp["transition_result"] = transition_result
    return JSONResponse(resp)


async def drift_detection(request: Request) -> JSONResponse:
    """GET /validate/drift/{plan_id} -- Detect criteria drift from parent plan."""
    plan_id = request.path_params["plan_id"]
    store = task_db.plan_store
    if store is None:
        return JSONResponse({"error": "plan store not initialized"}, status_code=500)

    plan = store.get_plan(plan_id)
    if not plan:
        return JSONResponse({"error": f"plan {plan_id} not found"}, status_code=404)

    parent_id = plan.get("parent_plan_id")
    if not parent_id:
        return JSONResponse({"drift": None, "message": "no parent plan (not a respec)"})

    parent = store.get_plan(parent_id)
    if not parent:
        return JSONResponse({"error": f"parent plan {parent_id} not found"}, status_code=404)

    drift = detect_drift(parent, plan)
    return JSONResponse(drift.to_json())


# ---------------------------------------------------------------------------
# Improvement Engine endpoints (v2 Phase 10)
# ---------------------------------------------------------------------------

async def improvement_patterns(request: Request) -> JSONResponse:
    """GET /improvement/patterns -- Pattern correlations across plan data."""
    store = task_db.plan_store
    if store is None:
        return JSONResponse({"error": "plan store not initialized"}, status_code=500)
    return JSONResponse(analyze_patterns(store))


async def improvement_thresholds(request: Request) -> JSONResponse:
    """GET /improvement/thresholds -- Learned retry budgets, complexity levels, quality weights."""
    store = task_db.plan_store
    if store is None:
        return JSONResponse({"error": "plan store not initialized"}, status_code=500)
    return JSONResponse(learn_thresholds(store).to_json())


async def improvement_templates(request: Request) -> JSONResponse:
    """GET /improvement/templates -- Golden spec templates from subsystems with clean passes."""
    store = task_db.plan_store
    if store is None:
        return JSONResponse({"error": "plan store not initialized"}, status_code=500)
    templates = find_golden_templates(store)
    return JSONResponse({"templates": templates, "count": len(templates)})


async def improvement_suggestions(request: Request) -> JSONResponse:
    """GET /improvement/suggestions -- Proactive improvement recommendations."""
    store = task_db.plan_store
    if store is None:
        return JSONResponse({"error": "plan store not initialized"}, status_code=500)
    suggestions = generate_suggestions(store)
    return JSONResponse({"suggestions": suggestions, "count": len(suggestions)})


async def improvement_baseline(request: Request) -> JSONResponse:
    """GET /improvement/baseline -- v1 vs v2 baseline comparison."""
    store = task_db.plan_store
    if store is None:
        return JSONResponse({"error": "plan store not initialized"}, status_code=500)
    return JSONResponse(baseline_comparison(store))


async def improvement_full(request: Request) -> JSONResponse:
    """GET /improvement/analysis -- Full recursive improvement analysis."""
    store = task_db.plan_store
    if store is None:
        return JSONResponse({"error": "plan store not initialized"}, status_code=500)
    return JSONResponse(full_analysis(store))


# ---------------------------------------------------------------------------
# Quality Scoring endpoints (v2 Phase 9)
# ---------------------------------------------------------------------------

async def quality_score_task(request: Request) -> JSONResponse:
    """POST /quality/score/{task_id} -- Compute post-outcome quality score for a task.

    Body (optional): {pass_rate: "8/10", builder_claimed_pass: true, respec_count: 0}
    If body is empty, pulls data from plan record.
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)
    task_id = request.path_params["task_id"]

    try:
        body = await request.json()
    except Exception:
        body = {}

    # Look up plan for this task
    store = task_db.plan_store
    plan = store.get_plan_by_task(task_id) if store else None
    if not plan:
        return JSONResponse({"error": f"no plan found for task {task_id}"}, status_code=404)

    # Get task metadata
    meta = state._task_meta.get(task_id) or {}

    pass_rate = body.get("pass_rate") or plan.get("pass_rate")
    builder_claimed = body.get("builder_claimed_pass", False)
    respec_count = body.get("respec_count", plan.get("respec_count", 0) or 0)
    failure_categories = meta.get("failure_categories", [])
    status = meta.get("status", "")

    # Get pre-send score from plan
    pre_send_score = plan.get("quality_score")

    # Build a minimal pre-send breakdown if we have the score
    from task_analytics import QualityBreakdown
    pre_breakdown = None
    if pre_send_score is not None:
        pre_breakdown = QualityBreakdown(
            pre_send_score=pre_send_score,
            total_score=pre_send_score,
            phase="pre_send",
        )

    breakdown = score_post_outcome(
        pre_send_breakdown=pre_breakdown,
        pass_rate=pass_rate,
        builder_claimed_pass=builder_claimed,
        respec_count=respec_count,
        failure_categories=failure_categories,
        status=status,
    )

    # Store updated score on plan
    try:
        store.update_outcome(plan["plan_id"], quality_score=breakdown.total_score)
    except Exception:
        pass

    return JSONResponse({
        "task_id": task_id,
        "plan_id": plan["plan_id"],
        "quality_score": breakdown.total_score,
        "pre_send_score": breakdown.pre_send_score,
        "post_outcome_adjustment": breakdown.post_outcome_score,
        "factors": breakdown.factors,
    })


async def quality_metrics_endpoint(request: Request) -> JSONResponse:
    """GET /quality/metrics -- Aggregate quality metrics across all plans."""
    store = task_db.plan_store
    if store is None:
        return JSONResponse({"error": "plan store not initialized"}, status_code=500)
    metrics = qm_metrics(store)
    return JSONResponse(metrics)


# ---------------------------------------------------------------------------
# Task Queue + Webhook endpoints (v2 Phase 8)
# ---------------------------------------------------------------------------

async def queue_status(request: Request) -> JSONResponse:
    """GET /queue/status -- Current queue depth, active tasks, capacity."""
    if state._task_queue is None:
        return JSONResponse({"error": "queue not initialized"}, status_code=500)
    return JSONResponse(state._task_queue.metrics())


async def queue_tasks(request: Request) -> JSONResponse:
    """GET /queue/tasks -- List tasks currently waiting in the queue."""
    if state._task_queue is None:
        return JSONResponse({"tasks": [], "count": 0})
    tasks = state._task_queue.queued_tasks()
    return JSONResponse({"tasks": tasks, "count": len(tasks)})


async def webhook_register(request: Request) -> JSONResponse:
    """POST /webhooks/register -- Register a webhook for an agent.

    Body: {agent: "pm", url: "http://localhost:9802/hook", events: ["message"]}
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    agent = body.get("agent")
    url = body.get("url")
    if not agent or not url:
        return JSONResponse({"error": "agent and url required"}, status_code=400)
    events = body.get("events")
    if state._webhook_registry is None:
        return JSONResponse({"error": "webhook registry not initialized"}, status_code=500)
    result = state._webhook_registry.register(agent, url, events)
    status = 201 if result.get("registered") else 400
    return JSONResponse(result, status_code=status)


async def webhook_unregister(request: Request) -> JSONResponse:
    """POST /webhooks/{webhook_id}/unregister -- Remove a webhook."""
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)
    webhook_id = request.path_params["webhook_id"]
    if state._webhook_registry is None:
        return JSONResponse({"error": "webhook registry not initialized"}, status_code=500)
    removed = state._webhook_registry.unregister(webhook_id)
    if removed:
        return JSONResponse({"status": "ok", "webhook_id": webhook_id})
    return JSONResponse({"error": "webhook not found"}, status_code=404)


async def webhook_list(request: Request) -> JSONResponse:
    """GET /webhooks -- List webhook registrations, optional ?agent= filter."""
    if state._webhook_registry is None:
        return JSONResponse({"webhooks": [], "count": 0})
    agent = request.query_params.get("agent")
    regs = state._webhook_registry.list_registrations(agent)
    return JSONResponse({"webhooks": regs, "count": len(regs)})


async def webhook_metrics(request: Request) -> JSONResponse:
    """GET /webhooks/metrics -- Webhook delivery stats."""
    if state._webhook_registry is None:
        return JSONResponse({"error": "webhook registry not initialized"}, status_code=500)
    return JSONResponse(state._webhook_registry.metrics())


# ---------------------------------------------------------------------------
# PM Autonomy endpoints (v2 Phase 7)
# ---------------------------------------------------------------------------

async def pm_actions_list(request: Request) -> JSONResponse:
    """GET /pm/actions -- List PM decisions with optional filters.

    Query params: ?action_type=auto_qa, ?status=completed, ?limit=50
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)
    if state._action_store is None:
        return JSONResponse({"actions": [], "count": 0})
    action_type = request.query_params.get("action_type")
    status = request.query_params.get("status")
    limit = int(request.query_params.get("limit", "50"))
    actions = state._action_store.list_actions(action_type=action_type, status=status, limit=limit)
    return JSONResponse({"actions": actions, "count": len(actions)})


async def pm_actions_outcome(request: Request) -> JSONResponse:
    """POST /pm/actions/{decision_id}/outcome -- Record whether a PM decision was correct.

    Body: {"correct": true|false}
    Used by Brad or QA results to train the autonomy expansion protocol.
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)
    decision_id = request.path_params["decision_id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    correct = body.get("correct")
    if correct is None:
        return JSONResponse({"error": "correct (true/false) required"}, status_code=400)
    if state._action_store is None:
        return JSONResponse({"error": "action store not initialized"}, status_code=500)
    state._action_store.update_status(decision_id, "completed", outcome_correct=bool(correct))
    return JSONResponse({"status": "ok", "decision_id": decision_id, "outcome_correct": bool(correct)})


async def pm_autonomy_status(request: Request) -> JSONResponse:
    """GET /pm/autonomy -- Current autonomy tier assignments and stats."""
    if state._action_store is None:
        return JSONResponse({"tiers": {}, "stats": {}})
    tiers = get_confidence_map()
    stats = {}
    for action_type in tiers:
        stats[action_type] = state._action_store.action_stats(action_type)
    return JSONResponse({"tiers": tiers, "stats": stats})


async def pm_autonomy_evaluate(request: Request) -> JSONResponse:
    """POST /pm/autonomy/evaluate -- Run autonomy expansion protocol.

    Evaluates all action types and promotes/demotes based on outcome data.
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)
    if state._action_store is None:
        return JSONResponse({"error": "action store not initialized"}, status_code=500)
    result = evaluate_autonomy(state._action_store)
    return JSONResponse(result)


async def pm_auto_approve_check(request: Request) -> JSONResponse:
    """POST /pm/actions/auto-approve -- Check and execute pending auto-approvals.

    MEDIUM-tier decisions that have passed their 30-min window get auto-approved.
    """
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)
    if state._action_store is None:
        return JSONResponse({"approved": [], "count": 0})
    pending = state._action_store.pending_auto_approvals()
    approved = []
    for action in pending:
        state._action_store.update_status(action["decision_id"], "approved")
        approved.append(action["decision_id"])
        state.logger.info("Auto-approved PM action %s (type=%s, created=%s)",
                     action["decision_id"], action["action_type"], action["created_at"])
    return JSONResponse({"approved": approved, "count": len(approved)})


# ---------------------------------------------------------------------------
# Knowledge governance endpoints (v2 Phase 6)
# ---------------------------------------------------------------------------

async def knowledge_governance_stats(request: Request) -> JSONResponse:
    """GET /knowledge/governance -- Knowledge governance metrics."""
    metrics = kg_metrics()
    return JSONResponse(metrics)


async def knowledge_prune(request: Request) -> JSONResponse:
    """POST /knowledge/prune -- Trigger stale knowledge pruning."""
    client = state._check_auth(request)
    if client is None:
        return JSONResponse({"error": "authorization required"}, status_code=401)
    result = prune_stale_knowledge(state._persist_manager)
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Brain health proxy
# ---------------------------------------------------------------------------

async def brain_health(request: Request) -> JSONResponse:
    """GET /brain/health -- Proxies to forge-brain health endpoint on Kush."""
    brain_url = config.FORGE_BRAIN_URL.rstrip("/").replace("/mcp", "")
    # Try health endpoint at base:8301/health first, fallback to base:8300/health
    health_urls = [
        brain_url.replace(":8300", ":8301") + "/health",
        brain_url + "/health",
    ]

    for url in health_urls:
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {config.FORGE_BRAIN_TOKEN}"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
                data["_proxy_source"] = url
                data["_proxy_ok"] = True
                data["circuit_breaker"] = state._persist_manager.circuit_state
                data["persist_queue_depth"] = state._persist_manager.queue_depth()
                data["dead_letter_depth"] = state._persist_manager.dead_letter_depth()
                return JSONResponse(data)
        except urllib.error.HTTPError as e:
            # Got a response, parse it
            try:
                data = json.loads(e.read().decode())
                data["_proxy_source"] = url
                data["_proxy_ok"] = False
                data["_http_status"] = e.code
                data["circuit_breaker"] = state._persist_manager.circuit_state
                return JSONResponse(data, status_code=200)
            except Exception:
                pass
        except Exception as e:
            last_error = str(e)
            continue

    return JSONResponse({
        "status": "unreachable",
        "error": last_error if "last_error" in locals() else "all health URLs failed",
        "circuit_breaker": state._persist_manager.circuit_state,
        "persist_queue_depth": state._persist_manager.queue_depth(),
        "dead_letter_depth": state._persist_manager.dead_letter_depth(),
        "_proxy_ok": False,
    })


# ---------------------------------------------------------------------------
# Infrastructure status
# ---------------------------------------------------------------------------

_INFRA_TOPOLOGY = [
    {
        "name": "Kush",
        "hostname": "kush",
        "ip": "kush.local",
        "role": "Brain Infrastructure",
        "services": [
            {"name": "Qdrant", "port": 6333, "path": "/healthz"},
            {"name": "forge-brain", "port": 8300, "path": "/health"},
            {"name": "forge-brain-health", "port": 8301, "path": "/health"},
        ],
    },
    {
        "name": "Haze",
        "hostname": "haze",
        "ip": "127.0.0.1",
        "role": "Development Machine",
        "services": [
            {"name": "Leroy A2A", "port": 9800, "path": "/health"},
            {"name": "Leroy Health", "port": 9801, "path": "/health"},
            {"name": "Dashboard", "port": 5173, "path": "/"},
        ],
    },
    {
        "name": "APEX",
        "hostname": "apex",
        "ip": "155.138.199.82",
        "role": "Carric Infrastructure (CloudRaider)",
        "services": [
            {"name": "A2A Gateway", "port": 8443, "path": "/health", "protocol": "https"},
        ],
    },
]


def _ping_service(ip: str, port: int, path: str, timeout: float = 2.0, protocol: str = "http") -> dict:
    """Attempt an HTTP/HTTPS GET to ip:port/path. Returns status dict."""
    url = f"{protocol}://{ip}:{port}{path}"
    start = time.time()
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed_ms = int((time.time() - start) * 1000)
            return {"status": "up", "http_status": resp.status, "latency_ms": elapsed_ms}
    except urllib.error.HTTPError as e:
        elapsed_ms = int((time.time() - start) * 1000)
        # Got response, even if error -- service is up
        return {"status": "up", "http_status": e.code, "latency_ms": elapsed_ms}
    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        return {"status": "down", "error": str(e)[:80], "latency_ms": elapsed_ms}


async def infra_status(request: Request) -> JSONResponse:
    """GET /infra/status -- Returns infrastructure status with health pings (parallel)."""
    now = datetime.now(timezone.utc).isoformat()
    loop = asyncio.get_running_loop()

    async def ping_svc(machine, svc):
        protocol = svc.get("protocol", "http")
        svc_status = await loop.run_in_executor(
            None, _ping_service, machine["ip"], svc["port"], svc["path"], 2.0, protocol
        )
        return {"name": svc["name"], "port": svc["port"], **svc_status}

    async def ping_machine(machine):
        service_results = await asyncio.gather(
            *[ping_svc(machine, svc) for svc in machine["services"]]
        )
        machine_up = any(s["status"] == "up" for s in service_results)
        return {
            "name": machine["name"],
            "hostname": machine["hostname"],
            "ip": machine["ip"],
            "role": machine["role"],
            "status": "up" if machine_up else "down",
            "services": list(service_results),
            "checked_at": now,
        }

    result = await asyncio.gather(*[ping_machine(m) for m in _INFRA_TOPOLOGY])
    return JSONResponse({"machines": list(result), "checked_at": now})
