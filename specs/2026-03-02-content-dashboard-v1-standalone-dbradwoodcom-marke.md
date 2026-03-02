---
spec_id: content-dashboard-v1-standalone-dbradwoodcom-marke
task_id: dc9f10d2-de37-4c7d-8292-544c049c741b
date: 2026-03-02
status: failed
pass_rate: 15/17 (SC-2 conditional fail: GET /content/today returned no brief because test draft is dated 2026-03-01 not today; 1 additional failure from truncated results)
retrospective: ## Retrospective **What worked in this spec:** 15 of 17 success criteria passed on first build. The standalone architecture directive was followed cleanly -- no Leroy dependencies detected. SQLite schema, dark theme, launchd plist, markdown parser, approval/reject/posted workflow, history view, and auto-refresh all implemented correctly. The spec's explicit file paths and machine details gave Leroy clear targets. Including all 17 criteria as binary pass/fail made QA straightforward to execute.  **What caused friction:** SC-2 (GET /content/today) was a conditional fail. The spec defined the endpoint but the test draft file was dated 2026-03-01, meaning "today" would naturally return empty unless the date matched. The spec should have either included a test fixture for today's date or explicitly stated the expected behavior when no brief exists for today. One additional test failure occurred but results were truncated by the Leroy API before the full detail could be captured.  **Spec improvement for next time:** When speccing date-relative endpoints like /content/today, explicitly define the fallback behavior (return most recent brief? return 404? return empty with message?) and provide test fixtures that cover the "today has no data" case. Also, for 17-criterion specs, consider breaking into backend-only and frontend-only QA passes to avoid result truncation. The Leroy result field has a length limit that clips long QA reports.
tags: []
---

# Content Dashboard v1 -- Standalone dbradwood.com Marketing Hub

## Objective

Build a standalone dashboard for Brad's content operation. It reads the content agent's output (daily media briefs, run logs, angle scores) and presents them in a clean UI with approve/reject workflow. This is a separate application from the Leroy dashboard. Different product, different lifecycle, different future.

## Why

The content agent runs daily at noon and writes drafts to `content/drafts/`. Right now Brad has to `cat` files in a terminal to see his content. That is not sustainable. He needs a dashboard where he can review today's angles, approve or reject them, see platform-specific drafts side by side, and track history.

This is step one of a full dbradwood.com marketing dashboard that will eventually include engagement tracking, SEO monitoring, and audience growth analytics.

## Architecture

### Standalone Application

- **Separate server process** from Leroy (port assigned by Ops)
- **Separate launchd plist** for lifecycle management
- **Same stack** as Leroy dashboard: React 18 + Vite + Tailwind + Python backend
- **Own data store**: SQLite database for approval state, post tracking, run history
- **Reads content agent output** from the filesystem (`~/Projects/leroy/content/`)
- **No dependency on Leroy server** -- does not import from or call Leroy endpoints
- **Shares forge-brain** for memory queries (content agent sessions, Aianna source material)

### Directory Structure

```
~/Projects/leroy/content/
  dashboard/           # <-- NEW: React frontend
    src/
      components/
      hooks/
      App.jsx
    index.html
    vite.config.js
    tailwind.config.js
    package.json
  server/              # <-- NEW: Python backend
    content_server.py
    content_db.py
    requirements.txt
  drafts/              # EXISTS: content agent writes here
  logs/                # EXISTS: content agent logs here
```

### Data Flow

```
Content Agent (noon daily)
  └─ writes content/drafts/YYYY-MM-DD.md
  └─ writes content/logs/agent-runs.json
  └─ writes content/logs/daily-media-YYYY-MM-DD.log

Content Server (always running)
  └─ reads drafts/ directory, parses markdown into structured data
  └─ serves parsed content via REST API
  └─ stores approval state in SQLite
  └─ tracks post status per angle per platform

Content Dashboard (browser)
  └─ displays today's content
  └─ approve/reject/edit workflow
  └─ run history
```

## Backend Endpoints

### Content API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/content/today` | GET | Returns today's parsed media brief: angles, scores, platform drafts, approval status |
| `/content/{date}` | GET | Returns parsed media brief for a specific date |
| `/content/history` | GET | Returns list of all dates with content, run status, approval counts. Query params: `?limit=30&offset=0` |
| `/content/{date}/angles/{index}/approve` | POST | Approve an angle. Body: `{platforms: ["blog", "linkedin", "x", "instagram"]}` |
| `/content/{date}/angles/{index}/reject` | POST | Reject an angle. Body: `{reason: "..."}` |
| `/content/{date}/angles/{index}/posted` | POST | Mark an angle as posted on a platform. Body: `{platform: "linkedin", url: "https://..."}` |
| `/health` | GET | Health check |

### Markdown Parser

The server must parse the content agent's markdown format. The existing draft format has a clear structure:

- H1: `# Daily Media Brief: YYYY-MM-DD`
- H2: `## Yesterday's Summary` (intro paragraph)
- H2: `## Content Angle N: {title}` (one per angle)
  - Metadata lines: Post-Worthiness Score, Target Angle, Source Sessions, Aianna Confidence, Status, Posted URLs
  - H3: `### Blog Post (dbradwood.com)` with YAML front matter block then markdown body
  - H3: `### LinkedIn` with post text
  - H3: `### X Thread` with numbered tweets
  - H3: `### Instagram` with CAPTION and CAROUSEL sections
- H2: `## Aianna Query Log` (table of queries run)

Parse this into a JSON structure. Each angle becomes an object with its metadata, scores, and platform drafts as separate fields. Store the parsed result in SQLite so repeated reads are fast (re-parse only when the file's mtime changes).

### SQLite Schema

```sql
-- Content briefs (one per day)
CREATE TABLE briefs (
    date TEXT PRIMARY KEY,           -- YYYY-MM-DD
    file_path TEXT NOT NULL,
    file_mtime REAL NOT NULL,        -- for cache invalidation
    summary TEXT,
    angles_count INTEGER,
    parsed_at TEXT NOT NULL           -- ISO8601
);

-- Individual angles
CREATE TABLE angles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_date TEXT NOT NULL REFERENCES briefs(date),
    angle_index INTEGER NOT NULL,    -- 0-based position in brief
    title TEXT NOT NULL,
    score INTEGER,                   -- post-worthiness score (1-10)
    target_angle TEXT,               -- one-line hook
    source_sessions TEXT,            -- comma-separated session IDs
    confidence TEXT,                 -- high/medium/low
    status TEXT DEFAULT 'draft',     -- draft/approved/rejected/posted
    rejected_reason TEXT,
    approved_at TEXT,
    rejected_at TEXT,
    UNIQUE(brief_date, angle_index)
);

-- Platform drafts (one per angle per platform)
CREATE TABLE platform_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    angle_id INTEGER NOT NULL REFERENCES angles(id),
    platform TEXT NOT NULL,          -- blog/linkedin/x/instagram
    content TEXT NOT NULL,           -- full draft text
    front_matter TEXT,               -- YAML front matter (blog only)
    carousel_slides TEXT,            -- JSON array (instagram only)
    posted_url TEXT,
    posted_at TEXT,
    UNIQUE(angle_id, platform)
);

-- Pipeline run history
CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    target_date TEXT,
    status TEXT NOT NULL,            -- success/failed/skipped/no_file
    reason TEXT,
    duration_seconds REAL,
    angles_found INTEGER,
    log_file TEXT
);
```

## Frontend

### Layout

Single-page app. Two views, toggled by a simple nav:

**1. Today (default view)**

Top bar: "Content Dashboard" title, date, pipeline status badge (green "Ran at 12:00" / red "Failed" / gray "Pending").

Below: one card per angle, stacked vertically. Each card shows:
- Angle title + score badge (e.g., "6/10" in a colored circle: green >= 7, yellow 5-6, red < 5)
- Target angle (one-line hook) as subtitle
- Status badge: Draft (gray) / Approved (green) / Rejected (red) / Posted (blue)
- Source sessions and Aianna confidence as small metadata
- Expand/collapse toggle to show platform drafts

Expanded angle card shows 4 platform tabs within the card:
- **Blog** -- rendered markdown with front matter visible
- **LinkedIn** -- plain text with character count
- **X** -- thread format, each tweet with character count
- **Instagram** -- caption text + carousel slide list

Action buttons at the bottom of each expanded card:
- "Approve" (green) -- marks angle as approved
- "Reject" (red) -- opens a small text input for reason
- "Mark Posted" -- dropdown to select platform + paste URL

**2. History view**

Reverse-chronological list of past dates. Each row shows:
- Date
- Pipeline status (success/fail/skip)
- Number of angles
- Approval status (e.g., "2 approved, 0 rejected")
- Click to expand and see the full brief for that date (same layout as Today view)

### Theme

Same dark theme as Leroy dashboard:
- Background: `#0f172a` (forge-bg / slate-900)
- Cards: `#1e293b` (forge-card / slate-800)
- Borders: `#334155` (forge-border / slate-700)
- Text: `#e2e8f0` (slate-200)
- Accent: `#3b82f6` (blue-500)
- Font: JetBrains Mono

### No New Dependencies

Use React 18, Vite, Tailwind 3.4. Same package.json pattern as Leroy dashboard. No additional npm packages.

## Success Criteria

1. Content server starts on its assigned port and serves `/health` returning `{"status": "ok"}`.
2. `GET /content/today` returns parsed media brief with angles, scores, and all 4 platform drafts per angle.
3. `GET /content/{date}` returns brief for any date that has a draft file.
4. `GET /content/history` returns list of all dates with run status and approval counts.
5. Dashboard renders today's angles with scores, titles, and status badges.
6. Clicking an angle expands to show all 4 platform drafts in tabbed view.
7. Approve button changes angle status to "approved" and persists to SQLite.
8. Reject button accepts a reason and changes angle status to "rejected."
9. Mark Posted accepts a platform and URL, records to SQLite, updates the status badge.
10. History view shows past dates with run status and approval summary.
11. Markdown parser correctly extracts all fields from the existing draft format (test against `content/drafts/2026-03-01.md`).
12. Dashboard auto-refreshes content every 60 seconds (simple poll, no SSE needed for v1).
13. Dark theme matches Leroy dashboard (forge-bg, forge-card, forge-border, JetBrains Mono).
14. Server reads from `~/Projects/leroy/content/drafts/` and `~/Projects/leroy/content/logs/` without modifying those files.
15. SQLite database created at `~/Projects/leroy/content/data/content.db`.
16. Launchd plist created for the content server with KeepAlive and RunAtLoad.
17. No dependency on Leroy server or Leroy dashboard. Fully standalone.

## Constraints

- React 18 + Vite + Tailwind (same stack as Leroy dashboard)
- Python backend (Starlette or FastAPI, match Leroy server patterns)
- SQLite for persistence (same patterns as Leroy's task_db.py)
- Port assigned by Ops (do not hardcode Leroy ports)
- All timestamps UTC ISO8601
- Read-only access to content agent files (drafts/, logs/). Never modify them.
- No authentication (localhost only)

## Do Not Do

- Do not modify the content agent, its persona, launcher, or output format
- Do not modify the Leroy dashboard or server
- Do not add engagement tracking, SEO, or analytics (that is v2+)
- Do not build a content editor (Brad edits in his preferred tool, not the dashboard)
- Do not add platform API integrations (posting is manual or via Cowork, not this dashboard)
- Do not add authentication
- Do not use WebSockets or SSE (simple polling is fine for v1)
- Do not add npm dependencies beyond what Leroy dashboard already uses

## Machine Details

- Haze: ~/Projects/leroy/content/ (content agent output + new dashboard + server)
- Content drafts: ~/Projects/leroy/content/drafts/*.md
- Content logs: ~/Projects/leroy/content/logs/
- Existing draft to test parser against: content/drafts/2026-03-01.md
- Existing run log: content/logs/agent-runs.json
- Leroy dashboard (reference for theme/patterns): ~/Projects/leroy/dashboard/
- Leroy server (reference for Python patterns): ~/Projects/leroy/server/

## Budget

Medium-complex. Markdown parser is the trickiest part. Frontend is straightforward card-based layout. Backend is simple CRUD. Launchd plist is boilerplate.

## Execution

Use agent teams. Suggested decomposition:
1. Backend: content_server.py + content_db.py + markdown parser + launchd plist
2. Frontend: React app with Today view + History view + theme
3. Integration: wire frontend to backend, test against real draft file
---
## Outcome
**Task ID:** dc9f10d2-de37-4c7d-8292-544c049c741b
**QA pass rate:** 15/17 (SC-2 conditional fail: GET /content/today returned no brief because test draft is dated 2026-03-01 not today; 1 additional failure from truncated results)

## Retrospective
## Retrospective
**What worked in this spec:** 15 of 17 success criteria passed on first build. The standalone architecture directive was followed cleanly -- no Leroy dependencies detected. SQLite schema, dark theme, launchd plist, markdown parser, approval/reject/posted workflow, history view, and auto-refresh all implemented correctly. The spec's explicit file paths and machine details gave Leroy clear targets. Including all 17 criteria as binary pass/fail made QA straightforward to execute.

**What caused friction:** SC-2 (GET /content/today) was a conditional fail. The spec defined the endpoint but the test draft file was dated 2026-03-01, meaning "today" would naturally return empty unless the date matched. The spec should have either included a test fixture for today's date or explicitly stated the expected behavior when no brief exists for today. One additional test failure occurred but results were truncated by the Leroy API before the full detail could be captured.

**Spec improvement for next time:** When speccing date-relative endpoints like /content/today, explicitly define the fallback behavior (return most recent brief? return 404? return empty with message?) and provide test fixtures that cover the "today has no data" case. Also, for 17-criterion specs, consider breaking into backend-only and frontend-only QA passes to avoid result truncation. The Leroy result field has a length limit that clips long QA reports.
