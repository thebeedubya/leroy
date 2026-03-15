"""Content Dashboard API Server.

Standalone Starlette server for Brad's content operation dashboard.
Reads content agent output from content/drafts/ and content/logs/.
Serves parsed content via REST API, stores approval state in SQLite.

Port: CONTENT_PORT env (default 9810)
"""

import json
import logging
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

import content_db
import markdown_parser

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log_level = os.environ.get("CONTENT_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    stream=sys.stderr,
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("content-server")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
WORK_DIR = Path(os.environ.get("CONTENT_WORK_DIR",
                               Path(__file__).parent.parent))
DRAFTS_DIR = WORK_DIR / "drafts"
LOGS_DIR = WORK_DIR / "logs"
RUNS_LOG = LOGS_DIR / "agent-runs.json"
DASHBOARD_DIST = WORK_DIR / "dashboard" / "dist"

PORT = int(os.environ.get("CONTENT_PORT", "9810"))
HOST = os.environ.get("CONTENT_HOST", "127.0.0.1")

_START_TIME = time.time()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return date.today().isoformat()


def _load_and_cache_brief(date_str: str) -> dict | None:
    """Load draft file for date, parse it, and cache in SQLite.
    Returns structured brief dict or None if no file exists.
    """
    draft_file = DRAFTS_DIR / f"{date_str}.md"
    if not draft_file.exists():
        return None

    db = content_db.get()
    file_mtime = draft_file.stat().st_mtime
    cached_mtime = db.get_brief_mtime(date_str)

    # Re-parse if file changed or not cached
    if cached_mtime is None or abs(file_mtime - cached_mtime) > 0.01:
        logger.info("Parsing brief for %s (mtime changed)", date_str)
        text = draft_file.read_text(encoding="utf-8")
        parsed = markdown_parser.parse_brief(text, date_str)

        db.upsert_brief(
            date=date_str,
            file_path=str(draft_file),
            file_mtime=file_mtime,
            summary=parsed.get("summary", ""),
            angles_count=len(parsed.get("angles", [])),
        )

        for angle_data in parsed.get("angles", []):
            angle_id = db.upsert_angle(
                brief_date=date_str,
                angle_index=angle_data["index"],
                title=angle_data["title"],
                score=angle_data.get("score"),
                target_angle=angle_data.get("target_angle"),
                source_sessions=angle_data.get("source_sessions"),
                confidence=angle_data.get("confidence"),
            )
            platforms = angle_data.get("platforms", {})
            for platform, pdata in platforms.items():
                db.upsert_platform_draft(
                    angle_id=angle_id,
                    platform=platform,
                    content=pdata.get("content", ""),
                    front_matter=pdata.get("front_matter"),
                    carousel_slides=json.dumps(pdata.get("carousel_slides")) if pdata.get("carousel_slides") else None,
                )

    return _assemble_brief(date_str)


def _assemble_brief(date_str: str) -> dict | None:
    """Assemble a complete brief response from SQLite."""
    db = content_db.get()
    brief_row = db.get_brief(date_str)
    if not brief_row:
        return None

    angles_rows = db.get_angles_for_date(date_str)
    angles = []
    for ar in angles_rows:
        drafts = db.get_drafts_for_angle(ar["id"])
        platforms = {}
        for d in drafts:
            platforms[d["platform"]] = {
                "content": d["content"],
                "front_matter": d.get("front_matter"),
                "carousel_slides": json.loads(d["carousel_slides"]) if d.get("carousel_slides") else None,
                "posted_url": d.get("posted_url"),
                "posted_at": d.get("posted_at"),
            }
        angles.append({
            "index": ar["angle_index"],
            "title": ar["title"],
            "score": ar["score"],
            "target_angle": ar["target_angle"],
            "source_sessions": ar["source_sessions"],
            "confidence": ar["confidence"],
            "status": ar["status"],
            "rejected_reason": ar.get("rejected_reason"),
            "approved_at": ar.get("approved_at"),
            "rejected_at": ar.get("rejected_at"),
            "platforms": platforms,
        })

    # Get latest pipeline run for this date
    run = db.get_latest_run_for_date(date_str)

    return {
        "date": date_str,
        "summary": brief_row["summary"],
        "angles_count": brief_row["angles_count"],
        "parsed_at": brief_row["parsed_at"],
        "pipeline_run": run,
        "angles": angles,
    }


def _sync_runs():
    """Load agent-runs.json and sync to SQLite."""
    if not RUNS_LOG.exists():
        return
    try:
        runs = json.loads(RUNS_LOG.read_text(encoding="utf-8"))
        content_db.get().sync_runs(runs)
    except Exception as e:
        logger.warning("Failed to sync runs: %s", e)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def health(request: Request) -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "uptime_seconds": round(time.time() - _START_TIME, 1),
        "version": "1.0.0",
    })


async def get_today(request: Request) -> JSONResponse:
    _sync_runs()
    today = _today()
    brief = _load_and_cache_brief(today)
    if brief is None:
        # Return a valid response even if no file yet
        db = content_db.get()
        run = db.get_latest_run_for_date(today)
        return JSONResponse({
            "date": today,
            "summary": None,
            "angles_count": 0,
            "parsed_at": None,
            "pipeline_run": run,
            "angles": [],
        })
    return JSONResponse(brief)


async def get_brief_by_date(request: Request) -> JSONResponse:
    date_str = request.path_params["date"]
    # Validate format
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return JSONResponse({"error": "Invalid date format. Use YYYY-MM-DD."}, status_code=400)

    _sync_runs()
    brief = _load_and_cache_brief(date_str)
    if brief is None:
        return JSONResponse({"error": f"No content found for {date_str}"}, status_code=404)
    return JSONResponse(brief)


async def get_history(request: Request) -> JSONResponse:
    _sync_runs()
    try:
        limit = int(request.query_params.get("limit", "30"))
        offset = int(request.query_params.get("offset", "0"))
    except ValueError:
        return JSONResponse({"error": "limit and offset must be integers"}, status_code=400)

    db = content_db.get()

    # Scan drafts dir for any files not yet in DB
    if DRAFTS_DIR.exists():
        for f in sorted(DRAFTS_DIR.glob("*.md")):
            date_str = f.stem
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
                _load_and_cache_brief(date_str)
            except ValueError:
                pass

    briefs = db.list_briefs(limit=limit, offset=offset)
    runs = {r["target_date"]: r for r in db.get_all_runs() if r.get("target_date")}

    history = []
    for b in briefs:
        d = b["date"]
        angles = db.get_angles_for_date(d)
        approved = sum(1 for a in angles if a["status"] == "approved")
        rejected = sum(1 for a in angles if a["status"] == "rejected")
        posted = sum(1 for a in angles if a["status"] == "posted")
        run = runs.get(d)
        history.append({
            "date": d,
            "angles_count": b["angles_count"],
            "approved": approved,
            "rejected": rejected,
            "posted": posted,
            "pipeline_run": run,
        })

    return JSONResponse({"history": history, "total": len(history)})


async def approve_angle(request: Request) -> JSONResponse:
    date_str = request.path_params["date"]
    try:
        angle_index = int(request.path_params["index"])
    except ValueError:
        return JSONResponse({"error": "index must be integer"}, status_code=400)

    db = content_db.get()
    ok = db.approve_angle(date_str, angle_index)
    if not ok:
        return JSONResponse({"error": "Angle not found"}, status_code=404)

    logger.info("Approved angle %d for %s", angle_index, date_str)
    return JSONResponse({"status": "approved", "date": date_str, "index": angle_index})


async def reject_angle(request: Request) -> JSONResponse:
    date_str = request.path_params["date"]
    try:
        angle_index = int(request.path_params["index"])
    except ValueError:
        return JSONResponse({"error": "index must be integer"}, status_code=400)

    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = body.get("reason", "")

    db = content_db.get()
    ok = db.reject_angle(date_str, angle_index, reason)
    if not ok:
        return JSONResponse({"error": "Angle not found"}, status_code=404)

    logger.info("Rejected angle %d for %s: %s", angle_index, date_str, reason)
    return JSONResponse({"status": "rejected", "date": date_str, "index": angle_index, "reason": reason})


async def mark_posted(request: Request) -> JSONResponse:
    date_str = request.path_params["date"]
    try:
        angle_index = int(request.path_params["index"])
    except ValueError:
        return JSONResponse({"error": "index must be integer"}, status_code=400)

    try:
        body = await request.json()
    except Exception:
        body = {}

    platform = body.get("platform")
    url = body.get("url", "")

    if not platform:
        return JSONResponse({"error": "platform is required"}, status_code=400)

    db = content_db.get()
    # Find angle_id
    angles = db.get_angles_for_date(date_str)
    angle_row = next((a for a in angles if a["angle_index"] == angle_index), None)
    if not angle_row:
        return JSONResponse({"error": "Angle not found"}, status_code=404)

    ok = db.mark_posted(angle_row["id"], platform, url)
    if not ok:
        return JSONResponse({"error": f"No draft found for platform '{platform}'"}, status_code=404)

    logger.info("Marked angle %d/%s as posted on %s: %s", angle_index, date_str, platform, url)
    return JSONResponse({
        "status": "posted",
        "date": date_str,
        "index": angle_index,
        "platform": platform,
        "url": url,
    })


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

routes = [
    Route("/health", health, methods=["GET"]),
    Route("/content/today", get_today, methods=["GET"]),
    Route("/content/history", get_history, methods=["GET"]),
    Route("/content/{date}", get_brief_by_date, methods=["GET"]),
    Route("/content/{date}/angles/{index}/approve", approve_angle, methods=["POST"]),
    Route("/content/{date}/angles/{index}/reject", reject_angle, methods=["POST"]),
    Route("/content/{date}/angles/{index}/posted", mark_posted, methods=["POST"]),
]

# Serve built frontend if dist/ exists
if DASHBOARD_DIST.exists():
    routes.append(Mount("/", StaticFiles(directory=str(DASHBOARD_DIST), html=True)))

app = Starlette(routes=routes)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def main():
    logger.info("Content server starting on %s:%d", HOST, PORT)
    logger.info("Drafts dir: %s", DRAFTS_DIR)
    logger.info("Logs dir: %s", LOGS_DIR)

    # Init DB
    content_db.init()
    _sync_runs()

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
