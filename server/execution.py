"""Leroy execution engine.

Extracted from server.py -- contains the Claude CLI subprocess runner,
worktree management, LeroyExecutor (A2A AgentExecutor), and agent card.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import selectors
import signal
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    AgentCard,
    AgentSkill,
    AgentCapabilities,
    HTTPAuthSecurityScheme,
    SecurityScheme,
)
from a2a.utils import new_agent_text_message

import config
import agent_bus
import task_db
from state_machine import TaskStateMachine, TaskState, IllegalTransitionError
from failure_taxonomy import classify_failure, FailureCategory, is_infra_failure, INFRA_CATEGORIES
from retry_budget import RetryBudget

logger = logging.getLogger("leroy-a2a")

# ---------------------------------------------------------------------------
# Module-level references -- set by main() in server.py before any execution
# ---------------------------------------------------------------------------
_task_meta = None
_state_machine = None
_retry_budget = None
_persist_manager = None
_dispatcher = None
_task_queue = None
_broadcast_task_update_sync = None
_emit_activity = None

# ---------------------------------------------------------------------------
# Claude CLI execution engine
# ---------------------------------------------------------------------------
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", shutil.which("claude") or "claude")
WORK_DIR = os.environ.get("LEROY_WORK_DIR", str(Path(__file__).parent.parent))
MAX_TASK_TIMEOUT = int(os.environ.get("LEROY_TASK_TIMEOUT", "3600"))  # 1 hour default
LOGS_DIR = Path(WORK_DIR) / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Terminal statuses -- tasks in these states will never transition again
_TERMINAL_STATUSES = frozenset({
    "failed", "completed", "cancelled", "completed_unverified",
    "completed_verified", "persisted", "archived", "escalated",
})

# Stuck task detection settings
_STUCK_CHECK_INTERVAL = 60  # seconds between checks
_STUCK_THRESHOLD = 120  # seconds after all subtasks done before flagging as stuck
_active_pids: dict[str, int] = {}  # task_id -> subprocess PID for liveness checks

# v2 Phase 1: Graduated timeout thresholds (replace flat inactivity timeout)
_GRADUATED_GRACE_MINUTES = 5     # 0-5 min: no warnings
_GRADUATED_WARN_MINUTES = 15     # 5-15 min: warn in metadata + SSE
_GRADUATED_KILL_MINUTES = 30     # 15-30 min: kill with partial capture
_PARTIAL_SNAPSHOT_INTERVAL = 60  # seconds between partial output snapshots
_FIRST_OUTPUT_GATE_SECONDS = 180  # Kill if zero stdout after 3 minutes

# System prompt injected into every claude -p invocation
LEROY_SYSTEM_PROMPT = """You are Leroy, the Engineering Lead for the FORGE ecosystem.
You receive specs from PM and execute them. You have full tool access.
Execute the spec completely. Return a structured result with:
- What was done
- Files created/modified
- Success criteria pass/fail
- Any issues encountered
Be thorough but concise. No filler.

BUILDER OUTPUT DISCIPLINE:
1. Before producing any deliverable, output three sections:
   [WHAT] Restate the task criteria in your own words.
   [REASONING] Explain your approach, assumptions, and tradeoffs.
   [OUTPUT] Then produce the actual deliverable.

2. Every 60 seconds of work, output: [PROGRESS] Working on: {current step}
   This resets the inactivity timer. Silence kills your session.

3. If blocked on something outside your control (service down, missing credentials,
   missing file), output: [BLOCKED] {description of what you need}
   This notifies the PM immediately.

4. Do not claim completion unless ALL criteria in the spec are addressed.
   If you cannot complete a criterion, explicitly state which ones and why.

When you need to communicate with PM during task execution, use the agent message bus:

  POST http://127.0.0.1:9800/messages
  Content-Type: application/json
  Body: {
    "from": "leroy",
    "to": "pm",
    "type": "question|status_update|decision_gate|blocker|deliverable_ready",
    "task_id": "<your LEROY_TASK_ID env var>",
    "content": "your message text",
    "context": "relevant background for PM",
    "requires_response": true|false
  }
  Returns: {"message_id": "...", "status": "queued"}

You can also message Ops for infrastructure requests:
  {"from": "leroy", "to": "ops", "type": "request", "content": "restart dashboard"}

Message types:
- status_update: non-blocking progress report, continues immediately
- deliverable_ready: non-blocking notification that work is ready for review
- question: BLOCKING -- wait for PM response before continuing
- decision_gate: BLOCKING -- PM picks from options before you continue
- blocker: BLOCKING -- you cannot proceed without PM input

For BLOCKING messages, after POSTing, poll for the response:
  GET http://127.0.0.1:9800/messages/{message_id}/response
  Poll every 5 seconds. Max wait 10 minutes.
  Returns: {"status": "pending"} or {"status": "answered", "response": "..."}

Your task_id is in the LEROY_TASK_ID environment variable.
Use it in every message so PM can route responses correctly.

Sub-task reporting: When you decompose work into sub-tasks and delegate to specialist agents, report each sub-task to the server so the dashboard can show execution progress:

  POST http://127.0.0.1:9800/tasks/{task_id}/subtasks
  Content-Type: application/json
  Body: {
    "subtask_id": "unique-id-for-this-subtask",
    "name": "What this subtask does (concise description)",
    "agent": "agent type (e.g. general-purpose, Explore, Plan)",
    "status": "running",
    "started_at": "<ISO timestamp>"
  }
  No auth required. Use the value of your LEROY_TASK_ID env var as {task_id}.
  POST when subtask starts, POST again when done:
  Body update: {"subtask_id": "same-id", "name": "same", "status": "completed", "output": "result summary", "completed_at": "<ISO timestamp>"}
  Use status "failed" if the subtask fails."""

# v2 Phase 3: Builder prompt version hash (computed once at module load)
_BUILDER_PROMPT_VERSION = hashlib.sha256(LEROY_SYSTEM_PROMPT.encode()).hexdigest()[:16]


def _setup_worktree(task_id: str) -> tuple[str | None, str | None]:
    """Create a git worktree for builder isolation. Returns (worktree_path, branch_name) or (None, None).

    Worktrees are created OUTSIDE the main project directory to prevent Claude Code
    from correlating builder sessions with Brad's active PM session. When worktrees
    lived under .claude/worktrees/ inside the project, Claude treated them as the
    same project context, causing ghost builds (zero stdout).
    """
    branch_name = f"task/{task_id[:8]}"
    worktree_base = os.environ.get("LEROY_WORKTREE_DIR", "/tmp/leroy-worktrees")
    worktree_path = os.path.join(worktree_base, task_id)
    try:
        os.makedirs(os.path.dirname(worktree_path), exist_ok=True)
        subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, worktree_path],
            cwd=WORK_DIR, check=True, capture_output=True, text=True, timeout=30,
        )
        logger.info("Task %s: worktree created at %s (branch %s)", task_id, worktree_path, branch_name)
        return worktree_path, branch_name
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning("Task %s: worktree creation failed (%s), falling back to project root", task_id, e)
        return None, None


def _cleanup_worktree(task_id: str, worktree_path: str | None, success: bool) -> None:
    """Clean up worktree after task. On success: preserve for review. On failure: remove."""
    if not worktree_path:
        return
    if success:
        logger.info("Task %s: preserving worktree at %s for review", task_id, worktree_path)
        return
    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", worktree_path],
            cwd=WORK_DIR, capture_output=True, text=True, timeout=30,
        )
        logger.info("Task %s: worktree cleaned up", task_id)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning("Task %s: worktree cleanup failed: %s", task_id, e)


def _parse_token_usage(output: str) -> dict | None:
    """Parse token usage from claude -p output. Returns {input, output, estimated_cost_usd} or None."""
    import re
    input_match = re.search(r"Input tokens:\s*([\d,]+)", output)
    output_match = re.search(r"Output tokens:\s*([\d,]+)", output)
    if input_match and output_match:
        input_tokens = int(input_match.group(1).replace(",", ""))
        output_tokens = int(output_match.group(1).replace(",", ""))
        # Sonnet pricing: $3/MTok input, $15/MTok output
        cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000
        return {"input": input_tokens, "output": output_tokens, "estimated_cost_usd": round(cost, 4)}
    return None


def _get_graduated_timeout(task_id: str) -> int:
    """Get the kill timeout in seconds. Spec override > graduated default."""
    meta = _task_meta.get(task_id) or {}
    override = meta.get("inactivity_timeout")
    if override is not None:
        try:
            return int(override) * 60  # override is in minutes
        except (ValueError, TypeError):
            pass
    return _GRADUATED_KILL_MINUTES * 60


def _run_claude_sync(task_id: str, spec: str) -> None:
    """Run claude -p in a subprocess with real-time log streaming.

    v2 Phase 1: partial output snapshots, [PROGRESS]/[BLOCKED] heartbeat parsing,
    graduated inactivity timeout, git worktree isolation, FD cleanup, token capture.
    """
    log_file = LOGS_DIR / f"{task_id}.log"
    kill_timeout = _get_graduated_timeout(task_id)
    logger.info("Task %s: spawning claude -p (kill_timeout=%ds, log=%s)", task_id, kill_timeout, log_file)

    # v2: transition NEW -> RUNNING
    if _state_machine:
        try:
            _state_machine.transition(task_id, TaskState.RUNNING, reason="builder_launched")
        except (IllegalTransitionError, KeyError) as e:
            logger.warning("v2 transition to RUNNING failed for %s: %s", task_id, e)
            _task_meta[task_id]["status"] = "working"
    else:
        _task_meta[task_id]["status"] = "working"
    _task_meta[task_id]["log_file"] = str(log_file)
    _task_meta[task_id]["last_activity"] = datetime.now(timezone.utc).isoformat()
    _broadcast_task_update_sync(task_id)

    # v2 Phase 1: worktree isolation
    worktree_path, branch_name = _setup_worktree(task_id)
    cwd = worktree_path or WORK_DIR
    if worktree_path:
        _task_meta[task_id]["worktree_path"] = worktree_path
        _task_meta[task_id]["worktree_branch"] = branch_name

    # v2 Phase 5B: Query brain for builder context
    builder_system_prompt = LEROY_SYSTEM_PROMPT
    try:
        _plan_store = task_db.plan_store
        _plan = _plan_store.get_plan_by_task(task_id) if _plan_store else None
        _target = _plan.get("target") if _plan else None
        _subsystem = _plan.get("subsystem") if _plan else None
        _subject = _plan.get("subject", "") if _plan else ""
        if _target or _subsystem:
            _ctx = _persist_manager.build_builder_context(_target, _subsystem, _subject)
            if _ctx.get("injected") and _ctx.get("context_text"):
                builder_system_prompt = LEROY_SYSTEM_PROMPT + "\n\n" + _ctx["context_text"]
                logger.info("Task %s: injected builder context (%d chars, %d results)",
                            task_id, len(_ctx["context_text"]), _ctx.get("result_count", 0))
                if _plan_store and _plan:
                    try:
                        _plan_store.update_brain_fields(_plan["plan_id"], builder_context_injected=True)
                    except Exception as _ube:
                        logger.warning("Task %s: failed to update builder_context_injected: %s", task_id, _ube)
    except Exception as _bce:
        logger.warning("Task %s: builder context query failed (using base prompt): %s", task_id, _bce)

    proc = None
    task_success = False
    try:
        with open(log_file, "w") as lf:
            lf.write(f"=== Task {task_id} started at {datetime.now(timezone.utc).isoformat()} ===\n")
            lf.write(f"=== Spec length: {len(spec)} chars ===\n")
            if worktree_path:
                lf.write(f"=== Worktree: {worktree_path} (branch: {branch_name}) ===\n")
            lf.write("\n")
            lf.flush()

            # Change 2: Pass spec via stdin to avoid ARG_MAX limit (macOS: 131KB).
            # Remove -p from argv; Claude CLI reads the prompt from stdin when no -p is given.
            proc = subprocess.Popen(
                [
                    CLAUDE_BIN,
                    "--output-format", "stream-json",
                    "--verbose",
                    "--system-prompt", builder_system_prompt,
                    "--dangerously-skip-permissions",
                    "--no-session-persistence",
                    "--model", "sonnet",
                    "--setting-sources", "user",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=cwd,
                env={k: v for k, v in os.environ.items() if k != "CLAUDECODE"} | {
                    "CLAUDE_CODE_ENTRYPOINT": "leroy-a2a",
                    "LEROY_TASK_ID": task_id,
                    "ENABLE_TOOL_SEARCH": "true",
                },
                start_new_session=True,
            )
            # Write spec to stdin then close the pipe so Claude sees EOF and starts processing.
            proc.stdin.write(spec)
            proc.stdin.close()

            _active_pids[task_id] = proc.pid
            logger.info("Task %s: claude PID %d (cwd=%s, spec_len=%d)", task_id, proc.pid, cwd, len(spec))

            # Change 3: Early process health check -- detect instant crashes before the
            # 180s first-output gate fires (bad arguments, missing entrypoint, etc.).
            time.sleep(3)
            if proc.poll() is not None:
                rc = proc.returncode
                try:
                    early_stderr = proc.stderr.read()
                except Exception:
                    early_stderr = ""
                logger.error(
                    "Task %s: Claude exited immediately rc=%d stderr=%s",
                    task_id, rc, early_stderr[:500],
                )
                raise subprocess.SubprocessError(
                    f"Claude exited immediately rc={rc}: {early_stderr[:200]}"
                )

            stdout_lines = []       # raw JSON lines from stream-json
            result_text_parts = []  # extracted text content for the final result
            stream_result = None    # final result text from the "result" event
            stderr_lines = []
            last_snapshot_time = time.time()
            last_activity_time = time.time()
            task_start_time = time.time()
            warned_inactivity = False
            first_output_received = False
            builder_sections: dict[str, str] = {}  # WHAT, REASONING, OUTPUT

            sel = selectors.DefaultSelector()
            sel.register(proc.stdout, selectors.EVENT_READ, "stdout")
            sel.register(proc.stderr, selectors.EVENT_READ, "stderr")

            deadline = time.time() + MAX_TASK_TIMEOUT
            open_streams = 2

            while open_streams > 0:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(CLAUDE_BIN, MAX_TASK_TIMEOUT)

                events = sel.select(timeout=min(remaining, 5.0))

                now = time.time()

                # v2 Phase 1: partial output snapshot every 60s
                if now - last_snapshot_time >= _PARTIAL_SNAPSHOT_INTERVAL:
                    partial = "".join(result_text_parts) if result_text_parts else "".join(stdout_lines)
                    if partial:
                        _task_meta[task_id]["partial_result"] = partial[-10000:]  # last 10k chars
                    last_snapshot_time = now

                # v2 Phase 1: graduated inactivity timeout
                inactivity = now - last_activity_time
                elapsed_minutes = (now - task_start_time) / 60

                # First-output gate: kill fast if builder never produces stdout
                if not first_output_received and inactivity > _FIRST_OUTPUT_GATE_SECONDS:
                    logger.warning(
                        "Task %s: first-output gate triggered (%ds, zero stdout)",
                        task_id, int(inactivity),
                    )
                    raise subprocess.TimeoutExpired(CLAUDE_BIN, int(inactivity))

                if elapsed_minutes > _GRADUATED_GRACE_MINUTES:
                    if inactivity > kill_timeout:
                        # Kill with partial capture
                        logger.warning(
                            "Task %s: inactivity timeout (%ds since last output, %d min elapsed)",
                            task_id, int(inactivity), int(elapsed_minutes),
                        )
                        raise subprocess.TimeoutExpired(CLAUDE_BIN, int(inactivity))

                    if not warned_inactivity and inactivity > _GRADUATED_WARN_MINUTES * 60:
                        warned_inactivity = True
                        _task_meta[task_id]["inactivity_warning"] = datetime.now(timezone.utc).isoformat()
                        _broadcast_task_update_sync(task_id)
                        logger.warning("Task %s: inactivity warning at %d min", task_id, int(inactivity / 60))

                if not events:
                    if proc.poll() is not None:
                        # Process exited -- drain remaining output
                        for _ in range(10):
                            drain = sel.select(timeout=0.5)
                            if not drain:
                                break
                            for dk, _ in drain:
                                line = dk.fileobj.readline()
                                if not line:
                                    sel.unregister(dk.fileobj)
                                    open_streams -= 1
                                    continue
                                last_activity_time = time.time()
                                _task_meta[task_id]["last_activity"] = datetime.now(timezone.utc).isoformat()
                                if dk.data == "stdout":
                                    stdout_lines.append(line)
                                    lf.write(line)
                                    lf.flush()
                                else:
                                    stderr_lines.append(line)
                                    lf.write(f"[STDERR] {line}")
                                    lf.flush()
                        break
                    continue

                for key, _ in events:
                    line = key.fileobj.readline()
                    if not line:
                        sel.unregister(key.fileobj)
                        open_streams -= 1
                        continue

                    last_activity_time = time.time()
                    warned_inactivity = False  # reset warning on activity
                    _task_meta[task_id]["last_activity"] = datetime.now(timezone.utc).isoformat()

                    if key.data == "stdout":
                        stdout_lines.append(line)
                        lf.write(line)
                        lf.flush()

                        if not first_output_received:
                            first_output_received = True
                            logger.info("Task %s: first output received at %.1fs", task_id, time.time() - task_start_time)

                        # Parse stream-json events for text extraction and heartbeats
                        try:
                            event = json.loads(line)
                            event_type = event.get("type", "")

                            if event_type == "assistant":
                                # Extract text from assistant message content
                                msg = event.get("message", {})
                                for part in msg.get("content", []):
                                    if part.get("type") == "text":
                                        text_chunk = part.get("text", "")
                                        if text_chunk:
                                            result_text_parts.append(text_chunk)

                            elif event_type == "result":
                                # Final result -- extract the complete text
                                stream_result = event.get("result", "")
                                # Also capture cost and token info
                                _task_meta[task_id]["stream_cost_usd"] = event.get("total_cost_usd")
                                _task_meta[task_id]["stream_usage"] = event.get("usage")

                        except (ValueError, KeyError):
                            pass  # Not valid JSON or missing fields -- treat as raw text

                        # v2 Phase 1: parse heartbeat markers from extracted text
                        text_for_heartbeat = line.strip()
                        # Also check result_text_parts for heartbeats (they may be in model output)
                        if result_text_parts:
                            text_for_heartbeat = result_text_parts[-1].strip()
                        if text_for_heartbeat.startswith("[PROGRESS]"):
                            progress_msg = text_for_heartbeat[len("[PROGRESS]"):].strip()
                            _task_meta[task_id]["last_progress"] = progress_msg
                            _task_meta[task_id]["last_progress_at"] = datetime.now(timezone.utc).isoformat()
                            logger.debug("Task %s: [PROGRESS] %s", task_id, progress_msg)

                        elif text_for_heartbeat.startswith("[BLOCKED]"):
                            block_msg = text_for_heartbeat[len("[BLOCKED]"):].strip()
                            _task_meta[task_id]["blocked_reason"] = block_msg
                            logger.warning("Task %s: [BLOCKED] %s", task_id, block_msg)
                            if _state_machine:
                                try:
                                    _state_machine.transition(task_id, TaskState.BLOCKED,
                                                              reason=f"builder_blocked: {block_msg}")
                                    # Dispatcher Phase 3a: route vehicle block for decision gating
                                    if _dispatcher is not None and _task_meta.get(task_id, {}).get("parent_id"):
                                        _dispatcher.handle_vehicle_blocked(
                                            task_id, reason=block_msg
                                        )
                                except (IllegalTransitionError, KeyError) as e:
                                    logger.warning("v2 BLOCKED transition failed: %s", e)
                        elif text_for_heartbeat.startswith("[WHAT]"):
                            builder_sections["what"] = text_for_heartbeat[len("[WHAT]"):].strip()
                        elif text_for_heartbeat.startswith("[REASONING]"):
                            builder_sections["reasoning"] = text_for_heartbeat[len("[REASONING]"):].strip()
                        elif text_for_heartbeat.startswith("[OUTPUT]"):
                            builder_sections["output"] = text_for_heartbeat[len("[OUTPUT]"):].strip()
                    else:
                        stderr_lines.append(line)
                        lf.write(f"[STDERR] {line}")
                        lf.flush()

                # Check if main process exited (orphan pipe guard)
                if open_streams > 0 and proc.poll() is not None:
                    for _ in range(10):
                        drain = sel.select(timeout=0.5)
                        if not drain:
                            break
                        for dk, _ in drain:
                            line = dk.fileobj.readline()
                            if not line:
                                sel.unregister(dk.fileobj)
                                open_streams -= 1
                                continue
                            last_activity_time = time.time()
                            _task_meta[task_id]["last_activity"] = datetime.now(timezone.utc).isoformat()
                            if dk.data == "stdout":
                                stdout_lines.append(line)
                                lf.write(line)
                                lf.flush()
                            else:
                                stderr_lines.append(line)
                                lf.write(f"[STDERR] {line}")
                                lf.flush()
                    logger.info(
                        "Task %s: main process exited (rc=%d) with %d pipe stream(s) still open. Stopping reader.",
                        task_id, proc.returncode, open_streams,
                    )
                    lf.write(
                        f"\n=== Main process exited rc={proc.returncode}, "
                        f"{open_streams} pipe stream(s) still open -- stopping reader ===\n"
                    )
                    lf.flush()
                    break

            sel.close()

            # v2 Phase 1: explicit FD cleanup to prevent orphan pipe hangs
            for fd in (proc.stdout, proc.stderr):
                try:
                    fd.close()
                except Exception:
                    pass

            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                logger.warning("Task %s: proc.wait() timed out -- killing", task_id)
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass

            # stream-json: extract final result text (prefer stream_result from
            # the "result" event, fall back to assembled text parts, then raw lines)
            stdout = stream_result or "".join(result_text_parts) or "".join(stdout_lines)
            stderr = "".join(stderr_lines)

            lf.write(f"\n=== Process exited with code {proc.returncode} at {datetime.now(timezone.utc).isoformat()} ===\n")
            lf.flush()

        _active_pids.pop(task_id, None)

        # v2 Phase 1: store builder discipline sections
        if builder_sections:
            _task_meta[task_id]["builder_sections"] = builder_sections

        # v2 Phase 1: capture token usage
        token_usage = _parse_token_usage(stdout + stderr)
        if token_usage:
            _task_meta[task_id]["token_usage"] = token_usage

        if proc.returncode == 0:
            task_success = True
            if _state_machine:
                try:
                    _state_machine.transition(task_id, TaskState.COMPLETED_UNVERIFIED, reason="builder_exit_0")
                except (IllegalTransitionError, KeyError) as e:
                    logger.warning("v2 transition to COMPLETED_UNVERIFIED failed: %s", e)
                    _task_meta[task_id]["status"] = "completed"
            else:
                _task_meta[task_id]["status"] = "completed"
            # Dispatcher Phase 3a: route vehicle completion for dependency gating
            if _dispatcher is not None and _task_meta.get(task_id, {}).get("parent_id"):
                _dispatcher.handle_vehicle_completed(task_id)
            _task_meta[task_id]["result"] = stdout
            logger.info("Task %s: completed (%d chars output)", task_id, len(stdout))
            _broadcast_task_update_sync(task_id)
            result_preview = (stdout[:400] + "...") if len(stdout) > 400 else stdout
            spec_preview = _task_meta[task_id].get("spec", "")[:120]
            agent_bus.send({
                "from": "leroy",
                "to": "pm",
                "type": "deliverable_ready",
                "task_id": task_id,
                "content": (
                    f"Task {task_id} COMPLETED successfully.\n\n"
                    f"Result preview:\n{result_preview}"
                ),
                "context": f"Spec preview: {spec_preview}",
                "requires_response": False,
            })
        else:
            result_text = f"Exit code {proc.returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
            if _state_machine and _retry_budget:
                try:
                    categories = classify_failure(result_text, _task_meta.get(task_id, {}))
                    _task_meta[task_id]["failure_categories"] = [c.value for c in categories]
                    _state_machine.transition(task_id, TaskState.FAILED_RETRYABLE,
                                              reason=f"exit_{proc.returncode}: {','.join(c.value for c in categories)}")
                    # Dispatcher Phase 3a: route vehicle failure for retry/decision gating
                    if _dispatcher is not None and _task_meta.get(task_id, {}).get("parent_id"):
                        _dispatcher.handle_vehicle_failed(
                            task_id, reason=f"exit_{proc.returncode}"
                        )
                    remaining = _retry_budget.consume_retry(task_id, categories)
                    if remaining <= 0 and not is_infra_failure(categories):
                        _state_machine.transition(task_id, TaskState.ESCALATED,
                                                  reason="retry_budget_exhausted")
                except (IllegalTransitionError, KeyError) as e:
                    logger.warning("v2 failure handling error: %s", e)
                    _task_meta[task_id]["status"] = "failed"
            else:
                _task_meta[task_id]["status"] = "failed"
            _task_meta[task_id]["result"] = result_text
            logger.error("Task %s: claude exited with code %d", task_id, proc.returncode)
            _broadcast_task_update_sync(task_id)
            agent_bus.send({
                "from": "leroy", "to": "pm",
                "type": "deliverable_ready",
                "task_id": task_id,
                "content": (
                    f"Task {task_id} FAILED (exit code {proc.returncode}).\n\n"
                    f"STDOUT:\n{stdout[:300]}\n"
                    f"STDERR:\n{stderr[:300]}"
                ),
                "context": f"Spec preview: {_task_meta[task_id].get('spec', '')[:120]}",
                "requires_response": False,
            })

    except subprocess.TimeoutExpired:
        _active_pids.pop(task_id, None)
        # v2 Phase 1: collect partial output before killing (stream-json: prefer extracted text)
        partial_output = ("".join(result_text_parts) if 'result_text_parts' in dir() and result_text_parts
                          else "".join(stdout_lines) if 'stdout_lines' in dir() else "")
        _task_meta[task_id]["partial_result"] = partial_output[-10000:] if partial_output else ""
        # Change 1: capture stderr so ghost builds (zero stdout) show WHY Claude crashed
        stderr_captured = "".join(stderr_lines) if 'stderr_lines' in dir() else ""

        if proc:
            # v2 Phase 1: explicit FD cleanup before kill
            for fd in (proc.stdout, proc.stderr):
                try:
                    fd.close()
                except Exception:
                    pass
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass

        timeout_result = partial_output if partial_output else f"Task timed out after {kill_timeout}s (no output)"
        # Change 1: append stderr to timeout result so ghost build crash reason is visible
        if stderr_captured:
            timeout_result += f"\n\n[STDERR]\n{stderr_captured}"
        if _state_machine and _retry_budget:
            try:
                categories = classify_failure(timeout_result, {**(_task_meta.get(task_id) or {}), "timeout": True})
                _task_meta[task_id]["failure_categories"] = [c.value for c in categories]
                _state_machine.transition(task_id, TaskState.FAILED_RETRYABLE,
                                          reason=f"inactivity_timeout_{kill_timeout}s")
                # Dispatcher Phase 3a: route vehicle timeout as failure
                if _dispatcher is not None and _task_meta.get(task_id, {}).get("parent_id"):
                    _dispatcher.handle_vehicle_failed(
                        task_id, reason=f"inactivity_timeout_{kill_timeout}s"
                    )
                remaining = _retry_budget.consume_retry(task_id, categories)
                if remaining <= 0:
                    _state_machine.transition(task_id, TaskState.ESCALATED,
                                              reason="retry_budget_exhausted_after_timeout")
            except (IllegalTransitionError, KeyError) as e:
                logger.warning("v2 timeout handling error: %s", e)
                _task_meta[task_id]["status"] = "failed"
        else:
            _task_meta[task_id]["status"] = "failed"
        _task_meta[task_id]["result"] = timeout_result
        logger.error("Task %s: timed out after %ds inactivity", task_id, kill_timeout)
        _broadcast_task_update_sync(task_id)
        agent_bus.send({
            "from": "leroy", "to": "pm",
            "type": "deliverable_ready",
            "task_id": task_id,
            "content": f"Task {task_id} TIMED OUT ({kill_timeout}s inactivity). Partial output: {len(partial_output)} chars.",
            "context": f"Spec preview: {_task_meta[task_id].get('spec', '')[:120]}",
            "requires_response": False,
        })
    except Exception as e:
        if _state_machine:
            try:
                categories = classify_failure(str(e), _task_meta.get(task_id) or {})
                _task_meta[task_id]["failure_categories"] = [c.value for c in categories]
                _state_machine.transition(task_id, TaskState.FAILED_RETRYABLE,
                                          reason=f"execution_error: {e}")
                # Dispatcher Phase 3a: route vehicle execution error as failure
                if _dispatcher is not None and _task_meta.get(task_id, {}).get("parent_id"):
                    _dispatcher.handle_vehicle_failed(
                        task_id, reason=f"execution_error: {e}"
                    )
            except (IllegalTransitionError, KeyError) as e2:
                logger.warning("v2 exception handling error: %s", e2)
                _task_meta[task_id]["status"] = "failed"
        else:
            _task_meta[task_id]["status"] = "failed"
        _task_meta[task_id]["result"] = f"Execution error: {e}"
        logger.exception("Task %s: execution error", task_id)
        _broadcast_task_update_sync(task_id)
        agent_bus.send({
            "from": "leroy", "to": "pm",
            "type": "deliverable_ready",
            "task_id": task_id,
            "content": f"Task {task_id} FAILED with execution error: {e}",
            "context": f"Spec preview: {_task_meta[task_id].get('spec', '')[:120]}",
            "requires_response": False,
        })
    finally:
        _task_meta[task_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        # v2 Phase 1: worktree cleanup
        _cleanup_worktree(task_id, worktree_path, task_success)
        # v2 Phase 5C: Brain persist now handled by state machine event handlers
        # (on_build_completed, on_build_failed). Only persist here as fallback.
        if not _state_machine:
            try:
                _persist_manager.persist_task(task_id, _task_meta[task_id])
            except Exception as _pe:
                logger.error("Task %s: persist_manager raised unexpectedly: %s", task_id, _pe)


async def _execute_task(task_id: str, spec: str) -> None:
    """Run claude execution in a thread pool so it doesn't block the server."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_claude_sync, task_id, spec)


# ---------------------------------------------------------------------------
# Agent Executor
# ---------------------------------------------------------------------------
class LeroyExecutor(AgentExecutor):
    """Receives specs from PM, executes via claude -p with dashboard visibility."""

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        # Extract the spec text from the incoming message
        task_id = context.task_id or uuid4().hex
        spec_text = ""
        if context.message:
            for part in context.message.parts:
                # Part is a RootModel; text lives in part.root.text
                if hasattr(part, "root") and hasattr(part.root, "text"):
                    spec_text += part.root.text

        # Store task metadata
        _task_meta[task_id] = {
            "task_id": task_id,
            "spec": spec_text,
            "status": "pending",
            "result": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "parent_id": None,  # set to container_id for dispatcher vehicles (Phase 3)
        }

        # v2: Initialize state machine for this task
        if _state_machine:
            try:
                _state_machine.initialize_task(task_id)
            except Exception as e:
                logger.warning("v2 state machine init failed for %s: %s", task_id, e)

        # v2 Phase 3: Store builder prompt version in task metadata
        _task_meta[task_id]["builder_prompt_version"] = _BUILDER_PROMPT_VERSION

        logger.info("Task %s received (spec length: %d chars) -- launching execution", task_id, len(spec_text))

        # Emit activity event for task creation
        spec_preview = spec_text[:100].replace("\n", " ") if spec_text else ""
        _emit_activity("leroy", "task_start", f"Task received: {spec_preview}...",
                       task_id=task_id)

        # Trigger persistence queue flush on task pickup (non-blocking)
        _persist_manager.flush_if_ready()

        # v2 Phase 8A: Enqueue through concurrency-controlled task queue
        # Extract target machine from spec header (## Target: kush)
        _target = "haze"
        for _line in (spec_text or "").split("\n")[:20]:
            if _line.strip().lower().startswith("## target:"):
                _t = _line.split(":", 1)[1].strip().lower()
                if _t in ("kush", "haze"):
                    _target = _t
                break

        # Store target in task metadata for filtering (e.g. kush_worker polls ?target=kush)
        _task_meta[task_id]["target"] = _target

        # Check for ## Atomic: true header (dispatcher must not slice atomic specs)
        _is_atomic = False
        for _line in (spec_text or "").split("\n")[:20]:
            if _line.strip().lower().startswith("## atomic:"):
                _is_atomic = _line.split(":", 1)[1].strip().lower() in ("true", "yes", "1")
                break

        # Dispatcher Phase 3a: check if spec needs slicing into vehicles
        # Skip dispatcher for atomic specs only. All targets (haze, kush, halo, studio)
        # get full dispatcher treatment. The dispatcher routes vehicles to the correct
        # machine via target-aware _make_vehicle_runnable().
        _dispatched = False
        if _dispatcher is not None and not _is_atomic:
            try:
                from spec_analyzer import extract_typed_ir
                _typed_ir = extract_typed_ir(spec_text)
                if _dispatcher.should_dispatch(_typed_ir, spec_text):
                    _cid = _dispatcher.dispatch(
                        spec_text=spec_text,
                        typed_ir=_typed_ir,
                        task_id=task_id,
                        priority="normal",
                        target_machine=_target,
                    )
                    if _cid:
                        logger.info(
                            "Task %s dispatched as container %s with vehicles (target=%s)",
                            task_id, _cid, _target,
                        )
                        _task_meta[task_id]["status"] = "dispatched"
                        _dispatched = True
            except Exception as _de:
                logger.warning(
                    "Dispatcher intercept failed for task %s (fail-open to normal enqueue): %s",
                    task_id, _de,
                )
        elif _is_atomic:
            logger.info("Task %s marked ## Atomic: true -- skipping dispatcher", task_id)

        # Enqueue non-dispatched tasks.
        # Local machines (haze): push into the in-process TaskQueue.
        # Remote machines (kush, halo, studio): leave as 'pending' for remote workers to poll.
        if not _dispatched:
            if _target in ("haze",):
                _task_queue.enqueue(task_id, spec_text, priority="normal", target_machine=_target)
            else:
                logger.info("Task %s left pending for remote worker pickup (target=%s)", task_id, _target)

        _broadcast_task_update_sync(task_id)

        # Respond immediately via A2A protocol
        await event_queue.enqueue_event(
            new_agent_text_message(
                f"Task {task_id} received and executing. "
                f"Spec length: {len(spec_text)} chars. "
                f"Poll GET /tasks/{task_id} for status."
            )
        )

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        task_id = context.task_id
        if task_id and task_id in _task_meta:
            # NOTE: TaskState enum has no CANCELLED state; no state machine transition available.
            # State machine gap: cancelled tasks bypass event handlers by design limitation.
            _task_meta[task_id]["status"] = "cancelled"
            logger.info("Task %s cancelled", task_id)
            await event_queue.enqueue_event(
                new_agent_text_message(f"Task {task_id} cancelled.")
            )
        else:
            await event_queue.enqueue_event(
                new_agent_text_message(f"Task {task_id} not found.")
            )


# ---------------------------------------------------------------------------
# Agent Card
# ---------------------------------------------------------------------------
spec_skill = AgentSkill(
    id="receive_spec",
    name="Receive Engineering Spec",
    description=(
        "Receives a product spec from PM and queues it for engineering execution "
        "via the micro-sprint SDLC."
    ),
    tags=["spec", "engineering", "sdlc"],
    examples=["Build the A2A server for Leroy", "Fix the auth middleware"],
)

agent_card = AgentCard(
    name=config.AGENT_NAME,
    description=config.AGENT_DESCRIPTION,
    url=config.AGENT_URL,
    version=config.AGENT_VERSION,
    defaultInputModes=["text"],
    defaultOutputModes=["text"],
    capabilities=AgentCapabilities(),
    skills=[spec_skill],
    securitySchemes={
        "bearer": SecurityScheme(
            root=HTTPAuthSecurityScheme(scheme="bearer")
        ),
    },
    security=[{"bearer": []}],
)
