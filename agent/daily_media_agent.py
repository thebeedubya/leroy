#!/usr/bin/env python3
"""
Daily Media Agent
-----------------
Autonomous scheduled agent that generates content drafts from yesterday's
Aianna session activity. Invoked by launchd at 6:00 AM daily.

Behavior:
  - Checks for duplicate (skips if draft already exists for target date)
  - Runs the /daily-media skill via claude -p --dangerously-skip-permissions
  - Retries up to MAX_RETRIES times on failure (covers forge-brain downtime)
  - Sends macOS notification on success or failure
  - Logs all runs to content/logs/agent-runs.json

launchd handles sleep/wake: if the machine was asleep at 6 AM, launchd fires
this job on wake. Duplicate prevention ensures it only runs once per date.
"""

import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────

PROJECT_ROOT   = Path("/Users/brad.wood/Projects/leroy")
DRAFTS_DIR     = PROJECT_ROOT / "content" / "drafts"
LOGS_DIR       = PROJECT_ROOT / "content" / "logs"
AGENT_LOG      = LOGS_DIR / "agent-runs.json"

# claude binary discovered at install time; agent also searches PATH fallbacks
CLAUDE_BIN     = Path("/Users/brad.wood/.local/bin/claude")
CLAUDE_TIMEOUT = 600        # seconds per attempt (10 min; skill runs 8 queries)

MAX_RETRIES    = 3
RETRY_WAIT_SEC = 5 * 60    # 5 minutes between retries = 15 min total window


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_yesterday() -> date:
    return date.today() - timedelta(days=1)


def draft_file(d: date) -> Path:
    return DRAFTS_DIR / f"{d.isoformat()}.md"


def resolve_claude() -> str:
    """Return the path to the claude binary, searching fallback locations."""
    candidates = [
        CLAUDE_BIN,
        Path("/usr/local/bin/claude"),
        Path("/opt/homebrew/bin/claude"),
        Path.home() / ".local" / "bin" / "claude",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    # Last resort: rely on PATH
    return "claude"


def notify(message: str, title: str = "Daily Media Agent") -> None:
    """Send a macOS user notification. Non-fatal if it fails."""
    try:
        subprocess.run(
            [
                "osascript", "-e",
                f'display notification "{message}" with title "{title}"',
            ],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass  # Notifications are informational; never block execution


def append_log(record: dict) -> None:
    """Append a run record to agent-runs.json (creates file if absent)."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    runs: list = []
    if AGENT_LOG.exists():
        try:
            with open(AGENT_LOG) as f:
                runs = json.load(f)
        except (json.JSONDecodeError, OSError):
            runs = []
    runs.append(record)
    with open(AGENT_LOG, "w") as f:
        json.dump(runs, f, indent=2)


def log(msg: str) -> None:
    """Timestamped stdout print (captured by launchd to log file)."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


# ── Core execution ─────────────────────────────────────────────────────────────

def run_skill(claude_bin: str, attempt: int) -> tuple[bool, str]:
    """
    Invoke the /daily-media skill via `claude -p`.

    --dangerously-skip-permissions bypasses all tool-call permission dialogs
    and settings.json tool restrictions, allowing Write/Edit/MCP tools to run
    unattended. This is intentional for an autonomous agent.

    Returns (success: bool, output_or_error: str).
    """
    log(f"Attempt {attempt}/{MAX_RETRIES}: running claude -p /daily-media ...")

    env = {
        **os.environ,
        "HOME": str(Path.home()),
        # Ensure claude and any node/python tools are on PATH
        "PATH": (
            f"{Path.home()}/.local/bin"
            ":/usr/local/bin:/opt/homebrew/bin"
            ":/usr/bin:/bin:/usr/sbin:/sbin"
        ),
    }

    result = subprocess.run(
        [
            claude_bin,
            "--dangerously-skip-permissions",
            "-p", "/daily-media",
            "--output-format", "text",
        ],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=CLAUDE_TIMEOUT,
        env=env,
    )

    if result.returncode == 0:
        return True, result.stdout
    else:
        err = (result.stderr or result.stdout or "unknown error")[:600]
        return False, err


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    yesterday  = get_yesterday()
    started_at = datetime.now(timezone.utc).isoformat()

    log(f"Daily Media Agent starting — target date: {yesterday}")

    # ── Duplicate prevention ──────────────────────────────────────────────────
    if draft_file(yesterday).exists():
        log(f"Draft already exists for {yesterday}. Skipping.")
        append_log({
            "timestamp":   started_at,
            "target_date": str(yesterday),
            "status":      "skipped",
            "reason":      "draft_exists",
        })
        return

    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    claude_bin = resolve_claude()

    # ── Retry loop ────────────────────────────────────────────────────────────
    success    = False
    last_error = ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            success, output = run_skill(claude_bin, attempt)
        except subprocess.TimeoutExpired:
            success = False
            output  = f"claude timed out after {CLAUDE_TIMEOUT}s"
        except Exception as exc:
            success = False
            output  = str(exc)

        if success:
            last_error = ""
            break

        last_error = output
        log(f"Attempt {attempt} failed: {last_error[:200]}")

        if attempt < MAX_RETRIES:
            wait_min = RETRY_WAIT_SEC // 60
            log(f"Waiting {wait_min} min before retry...")
            time.sleep(RETRY_WAIT_SEC)

    completed_at = datetime.now(timezone.utc).isoformat()
    exists_now   = draft_file(yesterday).exists()

    # ── Evaluate outcome ──────────────────────────────────────────────────────
    if success and exists_now:
        content     = draft_file(yesterday).read_text()
        angle_count = content.count("### Blog Post")  # one per content angle

        if angle_count > 0:
            notify(
                f"Content drafts ready for {yesterday} ({angle_count} angle(s))."
                " Review at content/drafts/",
                "Daily Media - Drafts Ready",
            )
            status = "success"
        else:
            # Skill ran, file written, but no post-worthy sessions found
            notify(
                f"Daily media ran for {yesterday} — no compelling sessions found.",
                "Daily Media - No Content",
            )
            status = "no_content"

        log(f"Done. status={status}, angles={angle_count}, path={draft_file(yesterday)}")
        append_log({
            "timestamp":    started_at,
            "completed_at": completed_at,
            "target_date":  str(yesterday),
            "status":       status,
            "draft_count":  angle_count,
            "draft_path":   str(draft_file(yesterday)),
        })

    elif success and not exists_now:
        # claude exited 0 but the skill didn't write a file — unexpected
        log("Warning: claude exited 0 but draft file was not created.")
        notify(
            f"Daily media ran for {yesterday} but no draft file was written. Check logs.",
            "Daily Media - Warning",
        )
        append_log({
            "timestamp":    started_at,
            "completed_at": completed_at,
            "target_date":  str(yesterday),
            "status":       "no_file",
            "reason":       "claude_exited_0_but_no_draft",
        })

    else:
        log(f"FAILED after {MAX_RETRIES} attempts. Last error: {last_error[:300]}")
        notify(
            f"Daily media FAILED for {yesterday} after {MAX_RETRIES} attempts."
            " Check ~/Library/Logs/leroy-daily-media.log",
            "Daily Media - FAILED",
        )
        append_log({
            "timestamp":    started_at,
            "completed_at": completed_at,
            "target_date":  str(yesterday),
            "status":       "failed",
            "attempts":     MAX_RETRIES,
            "error":        last_error[:500],
        })
        sys.exit(1)


if __name__ == "__main__":
    main()
