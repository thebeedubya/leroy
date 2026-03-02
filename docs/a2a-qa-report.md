# Leroy A2A Server QA Report

**Date:** 2026-03-01
**Server version:** 0.2.0
**Test machine:** Haze (local)
**Server uptime at test start:** ~3100 seconds
**Executed by:** Leroy (Engineering Lead) — 5 parallel QA agents

---

## Summary

| Metric | Count |
|--------|-------|
| **Total tests** | **72** |
| **PASS** | **72** |
| **FAIL** | **0** |
| **ERROR** | **0** |

**Result: ALL PASS. No failures or regressions found.**

---

## Test Categories

| # | Category | Tests | Pass | Fail |
|---|----------|-------|------|------|
| 1 | Agent Card (`/.well-known/agent.json`) | T01–T05 | 5 | 0 |
| 2 | Health Endpoint (port 9801 + 9800) | T06–T10 | 5 | 0 |
| 3 | Authentication (bearer token) | T11–T15 | 5 | 0 |
| 4 | Task Submission via A2A Protocol | T16–T20 | 5 | 0 |
| 5 | Task Listing and Filtering | T21–T25 | 5 | 0 |
| 6 | Task Detail | T26–T28 | 3 | 0 |
| 7 | Error Handling | T29–T34 | 6 | 0 |
| 8 | Task Cancellation | T35–T39 | 5 | 0 |
| 9 | Custom Endpoint Error Handling | T40–T41 | 2 | 0 |
| 10 | PM Message Sending (Leroy → PM) | T42–T46 | 5 | 0 |
| 11 | PM Message Reading (PM side) | T47–T49 | 3 | 0 |
| 12 | PM Response Flow (full round-trip) | T50–T54 | 5 | 0 |
| 13 | MCP Client Tool Definitions | T55–T64 | 10 | 0 |
| 14 | Concurrent Task Handling | T65–T68 | 4 | 0 |
| 15 | Persistence Manager State | T69–T72 | 4 | 0 |

---

## Detailed Results

### Group 1 — Agent Card (`/.well-known/agent.json`)

| Test ID | Name | Result | Actual |
|---------|------|--------|--------|
| T01 | Agent card accessible (200 + JSON) | PASS | `200 OK, content-type=application/json` |
| T02 | Agent card has required fields | PASS | All 6 present: `name, description, url, version, skills, securitySchemes` |
| T03 | Skills array is non-empty | PASS | 1 skill: `receive_spec` |
| T04 | securitySchemes contains bearer scheme | PASS | Keys: `['bearer']` |
| T05 | Agent card accessible without auth | PASS | `200` (no Authorization header required) |

---

### Group 2 — Health Endpoint

| Test ID | Name | Result | Actual |
|---------|------|--------|--------|
| T06 | Health endpoint returns 200 with status ok | PASS | `200, status=ok` |
| T07 | Health response has required fields | PASS | All 6 present: `status, service, version, uptime_seconds, tasks, persistence` |
| T08 | uptime_seconds > 0 | PASS | `3103.7` |
| T09 | Main server `/health` responds | PASS | `200` on port 9800 (identical response) |
| T10 | auth_enabled is present and true | PASS | `auth_enabled=True` |

---

### Group 3 — Authentication

| Test ID | Name | Result | Actual |
|---------|------|--------|--------|
| T11 | GET `/tasks` with valid token | PASS | `200` |
| T12 | GET `/tasks` with invalid token | PASS | `401 {"error":"authorization required"}` |
| T13 | GET `/tasks` with no Authorization header | PASS | `401 {"error":"authorization required"}` |
| T14 | GET `/tasks` with wrong scheme (`Token abc`) | PASS | `401 {"error":"authorization required"}` |
| T15 | GET `/tasks` with empty Bearer token (`Bearer `) | PASS | `401 {"error":"authorization required"}` |

---

### Group 4 — Task Submission via A2A Protocol

| Test ID | Name | Result | Actual |
|---------|------|--------|--------|
| T16 | A2A task submission accepted | PASS | `200 OK, body keys=['id', 'jsonrpc', 'result']` |
| T17 | Response contains task ID | PASS | `task_id=cd390d3d-407c-446b-badc-47892dba844b` |
| T18 | GET `/tasks/{task_id}` returns 200 | PASS | `200 OK, all expected fields present` |
| T19 | Task has valid status field | PASS | `status=working` |
| T20 | Task has spec and created_at fields | PASS | Both present |

---

### Group 5 — Task Listing and Filtering

| Test ID | Name | Result | Actual |
|---------|------|--------|--------|
| T21 | GET `/tasks` returns tasks array and count | PASS | `200 OK, count=6` |
| T22 | GET `/tasks?status=completed` — all completed | PASS | `2 tasks, all status=completed` |
| T23 | GET `/tasks?status=pending` — all pending | PASS | `200 OK, 0 tasks (server had none pending)` |
| T24 | GET `/tasks?status=cancelled` — all cancelled | PASS | `200 OK, 0 tasks` |
| T25 | GET `/tasks/pending` dedicated endpoint | PASS | `200 OK, count=0` |

---

### Group 6 — Task Detail

| Test ID | Name | Result | Actual |
|---------|------|--------|--------|
| T26 | GET `/tasks/{valid_task_id}` returns 200 | PASS | `200 OK, full task object` |
| T27 | GET `/tasks/nonexistent-id` returns 404 | PASS | `404 Not Found` |
| T28 | Task object has all required fields | PASS | `task_id, spec, status, result, created_at, completed_at` all present |

---

### Group 7 — Error Handling

| Test ID | Name | Result | Actual |
|---------|------|--------|--------|
| T29 | POST `/tasks/complete` with malformed JSON → 400 | PASS | `400 Bad Request` |
| T30 | POST `/tasks/complete` with missing `task_id` → 400 | PASS | `400 Bad Request` |
| T31 | POST `/tasks/complete` with nonexistent `task_id` → 404 | PASS | `404 Not Found` |
| T32 | GET `/tasks/complete` (wrong method) → 404 | PASS | `404 Not Found` |
| T33 | Empty spec text accepted gracefully | PASS | `200 OK` — server accepts empty specs without crashing |
| T34 | 150KB oversized spec handled gracefully | PASS | `200 OK` — no crash, no hang, task registered |

---

### Group 8 — Task Cancellation

| Test ID | Name | Result | Actual |
|---------|------|--------|--------|
| T35 | Submit A2A spec, extract task_id | PASS | `task_id=5c46db68-9aa8-4ed5-a702-213072bb81cb` |
| T36 | Cancel pending/working task → 200 | PASS | `200 OK` |
| T37 | Cancelled task shows `status=cancelled` | PASS | `status=cancelled` confirmed |
| T38 | Re-cancel same task → 409 | PASS | `409 Conflict` |
| T39 | Cancel nonexistent task → 404 | PASS | `404 Not Found` |

---

### Group 9 — Custom Endpoint Error Handling

| Test ID | Name | Result | Actual |
|---------|------|--------|--------|
| T40 | GET `/tasks/{id}/cancel` (wrong method) → 405 | PASS | `405 Method Not Allowed` |
| T41 | POST `/tasks/{id}/cancel` without auth → 401 | PASS | `401 Unauthorized` |

---

### Group 10 — PM Message Sending (Leroy → PM)

| Test ID | Name | Result | Actual |
|---------|------|--------|--------|
| T42 | `status_update` accepted without auth | PASS | `200, message_id=6e69f05f777b4c7681f224976a32b085` |
| T43 | Missing `type` field → 400 | PASS | `400` |
| T44 | Missing `task_id` field → 400 | PASS | `400` |
| T45 | Invalid type `unknown_type` → 400 | PASS | `400` |
| T46 | Malformed JSON body → 400 | PASS | `400` |

---

### Group 11 — PM Message Reading (PM side)

| Test ID | Name | Result | Actual |
|---------|------|--------|--------|
| T47 | GET `/pm/messages/pending` with auth → 200 | PASS | `200, messages array present` |
| T48 | GET `/pm/messages` with auth → 200 with array and count | PASS | `200, has_messages=True, has_count=True` |
| T49 | GET `/pm/messages/pending` without auth → 401 | PASS | `401` |

---

### Group 12 — PM Response Flow (full round-trip)

| Test ID | Name | Result | Actual |
|---------|------|--------|--------|
| T50 | `question` type message accepted, `requires_response=true` | PASS | `200, message_id=a2113829778a4f7c88b6cc85baa936f5` |
| T51 | Poll before PM answers → `status=pending` | PASS | `200, status=pending` |
| T52 | PM responds via `/respond` → 200 | PASS | `200` |
| T53 | Poll after PM answers → `status=answered` with text | PASS | `200, status=answered, has_response=True` |
| T54 | Nonexistent `message_id` → 404 | PASS | `404` |

---

### Group 13 — MCP Client Tool Definitions

| Test ID | Name | Result | Actual |
|---------|------|--------|--------|
| T55 | `.mcp.json` exists and is valid JSON | PASS | Present at `/Users/brad.wood/Projects/leroy/.mcp.json` |
| T56 | `.mcp.json` has `leroy` server entry with `localhost:9800` | PASS | Entry found, URL correct |
| T57 | `mcp/` directory exists with implementation files | PASS | `leroy_client.py` and supporting files present |
| T58 | `leroy_send_spec` tool defined | PASS | Found in MCP code |
| T59 | `leroy_check_task` tool defined | PASS | Found in MCP code |
| T60 | `leroy_list_tasks` tool defined | PASS | Found in MCP code |
| T61 | `leroy_cancel_task` tool defined | PASS | Found in MCP code |
| T62 | `leroy_health` tool defined | PASS | Found in MCP code |
| T63 | `leroy_read_messages` tool defined | PASS | Found in MCP code |
| T64 | `leroy_reply_to_message` tool defined | PASS | Found in MCP code |

---

### Group 14 — Concurrent Task Handling

| Test ID | Name | Result | Actual |
|---------|------|--------|--------|
| T65 | Two specs submitted <1s apart — unique task IDs | PASS | `7192cf3b-7a9f-4e3f-97e5-005bd3923472` and `28a2e7e7-ea93-40ed-8fb4-e83d35a8ec33` |
| T66 | Both tasks registered in GET `/tasks` | PASS | `t1_found=True, t2_found=True, total_tasks=17` |
| T67 | Each task has distinct `task_id` | PASS | UUIDs confirmed distinct |
| T68 | Health endpoint responsive after concurrent submission | PASS | `200 OK` |

---

### Group 15 — Persistence Manager State

| Test ID | Name | Result | Actual |
|---------|------|--------|--------|
| T69 | `persistence.queue_depth` present, non-negative | PASS | `queue_depth=0` (no backlog) |
| T70 | `persistence.recent_log` present in health response | PASS | List field present |
| T71 | `content/logs/` directory exists | PASS | Found: `persist-queue.json, persist-log.json, daily-media-2026-03-01.log, .gitkeep` |
| T72 | `persist-queue.json` is valid JSON if it exists | PASS | File not present (no queued entries — brain is reachable) |

---

## Findings and Observations

### Finding 1: Task ID Extraction Requires Text Parsing (Low Severity)

**Observed by:** Groups 2, 3, and 5 (independently)
**Description:** The A2A `message/send` response embeds the task ID inside a human-readable text string in `parts[0].text` (e.g., `"Task cd390d3d-407c-446b-badc-47892dba844b received and executing..."`). There is no structured field in the JSON-RPC result for the task ID.
**Impact:** A2A clients that want to poll task status must parse the text response with a regex to extract the UUID. This works but is brittle. If the message format changes, all clients break silently.
**Recommendation:** Add a structured `taskId` field to the A2A response, alongside the human-readable message text.
**Blocker:** No. Current behavior functions correctly.

### Finding 2: Server Accepts Empty and Oversized Specs (Informational)

**Observed by:** Group 3 (T33, T34)
**Description:** A 150KB spec is accepted and queued (returns 200). An empty spec is accepted and queued. Both spawn a `claude -p` subprocess.
**Impact:** No validation gate on spec size or content before task creation. An empty spec will spawn a subprocess and consume resources unnecessarily. A 150KB spec is unlikely to be a real PM spec.
**Recommendation:** Consider adding a max spec length guard (e.g., 100KB) and a non-empty spec check at the A2A receive layer. Low priority — no production misuse expected from PM.
**Blocker:** No.

### Finding 3: Persistence Queue is Clean (Informational)

**Observed by:** Group 5 (T69–T72)
**Description:** At test time, `queue_depth=0` and no `persist-queue.json` was present. The most recent persist log entry shows a successful persist in 402ms. The circuit breaker is in `closed` state.
**Impact:** Positive — persistence pipeline is healthy. Kush is reachable and handling requests.

### Finding 4: Server Accumulated 17 Tasks During QA Run (Informational)

**Observed by:** Group 5 (T66)
**Description:** By the end of the test run, 17 tasks were registered in the server's in-memory store. This includes the 3 pre-existing tasks at test start plus the ~14 tasks submitted by the QA agents. All tasks are held in-memory; the server does not persist task state across restarts.
**Impact:** None for production use. In-memory store is by design for the current v0.2.0 implementation.

---

## Failures

**None.** All 72 tests passed.

---

## Environment at Test Time

| Item | Value |
|------|-------|
| Server version | 0.2.0 |
| Host | `127.0.0.1` |
| Main port | 9800 |
| Health port | 9801 |
| Auth enabled | Yes (3 tokens loaded) |
| Tokens file | `server/tokens/tokens.json` |
| Tasks at start | 3 (2 working, 1 completed) |
| Tasks at end | 17 |
| Persistence queue depth | 0 |
| forge-brain URL | `http://192.168.1.100:8300/sse` |
| Circuit breaker state | closed |
| Recent persist | 1 entry, success=True, 402ms |

---

## Artifact Cleanup

Test artifacts submitted to the server (task submissions T16, T35, T65, T66 and related) were left in the server's in-memory store. They will be cleared on server restart. No files were written to the local filesystem by the QA spec submissions (specs were trivial/cancellation tests). The test scripts at `/tmp/qa_group[1-5].py` and result files at `/tmp/leroy-qa-group[1-5].json` are in `/tmp` and will be cleared on next system restart.
