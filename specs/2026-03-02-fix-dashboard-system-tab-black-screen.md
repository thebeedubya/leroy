---
spec_id: fix-dashboard-system-tab-black-screen
task_id: 748a1db6-d36a-4019-9a71-93873d82dc54
date: 2026-03-02
status: completed
pass_rate: 10/10
retrospective: ## Retrospective **What worked in this spec:** The four-bug decomposition was precise. Each fix was scoped to a specific file and specific behavior, which let Leroy execute all four in a single pass without ambiguity. Naming the exact files and the exact failure mode (e.g., "locals() not dir()" for brain_health, "https:// for port 8443" for APEX) eliminated guesswork. Build completed in ~2.5 minutes. The constraint to preserve Promise.allSettled prevented a common regression path.  **What caused friction:** None observed. Clean 10/10 QA pass with no rework. The spec was tight enough that even the QA spec's 10 test cases all passed on first run. The only thing that could have been better: the spec didn't explicitly call out the loading indicator color requirements (text-slate-400 vs dark theme), but the QA spec caught that as Test 7 and it passed anyway because the fix naturally used visible colors.  **Spec improvement for next time:** When fixing UI rendering bugs on dark themes, explicitly state minimum contrast requirements or specific Tailwind color classes in the build spec, not just the QA spec. The build happened to get it right, but the spec should have been explicit about "loading indicator must be visible against forge-bg" as a build requirement, not just a QA check.
tags: []
---

# Fix Dashboard System Tab Black Screen

## Objective

The System tab renders a black screen when clicked. Four bugs are stacking up to produce this: slow endpoint with no loading feedback, silent fetch error masking, wrong protocol for APEX ping, and a scoping bug in brain health error reporting. Fix all four.

## Why

Brad clicked the System tab and got a black screen. The tab is fully implemented (component exists, routing works, endpoints exist) but the data path is broken in multiple places.

## Root Cause Analysis

1. **Slow endpoint, invisible loading state.** `/infra/status` pings 7 services with 2-second TCP timeouts. Up to 14 seconds before response. During that window, `SystemTab.jsx` renders "Checking system status..." in `text-slate-600` on `forge-bg`, which is visually indistinguishable from a blank screen.

2. **Silent fetch error masking.** `useSystem.js` calls `fetch().then(r => r.json())` without checking `r.ok`. If the server returns a non-200 response, the error JSON is silently treated as valid data. The component then tries to render undefined fields and shows nothing.

3. **APEX always reports down.** `_ping_service()` in `server.py` uses `http://` for all pings, including APEX port 8443 which requires HTTPS. The ping always fails (connection reset or SSL error), so APEX permanently shows as "down."

4. **Error message swallowed.** `brain_health()` checks `"last_error" in dir()` instead of `"last_error" in locals()`. `dir()` returns attribute names of the current object, not local variables. The check always evaluates False, so actual exception messages are replaced with the generic "all health URLs failed."

## Scope

### Fix 1: Frontend -- `dashboard/src/hooks/useSystem.js`

- Add `response.ok` check before calling `.json()`. If not ok, throw an Error with the HTTP status.
- Add a fetch timeout of 5 seconds using AbortController. If the fetch exceeds 5 seconds, abort and set the error state.
- Both fetches should still use `Promise.allSettled` so one failure doesn't block the other.

### Fix 2: Frontend -- `dashboard/src/components/tabs/SystemTab.jsx`

- Replace the loading state text (`text-slate-600`) with a visible loading indicator. Use a pulsing dot or "Loading..." in `text-slate-400` at minimum. Match the existing dashboard loading patterns.
- If both fetches error out, render an explicit error panel showing what failed (e.g., "Brain health: unreachable", "Infrastructure: request timed out"). Use `text-red-400` or similar visible error styling.
- If one fetch succeeds and one fails, render the successful panel and show an error message in the failed panel. Do not black-screen on partial failure.

### Fix 3: Backend -- `server/server.py` `infra_status()` / `_ping_service()`

- Update `_INFRA_TOPOLOGY` or `_ping_service()` to use `https://` for APEX port 8443.
- Verify all other service pings use the correct protocol (HTTP for local services, HTTPS for external).
- Keep the 2-second per-service timeout. Ensure services are pinged in parallel via the thread pool executor (they should be already, verify).

### Fix 4: Backend -- `server/server.py` `brain_health()`

- Change `"last_error" in dir()` to `"last_error" in locals()` so the actual exception message propagates to the response.

## Success Criteria

1. System tab renders visible content within 5 seconds of clicking.
2. Brain Health panel shows connection status: "connected" with stats if reachable, "unreachable" with error detail if not.
3. Infrastructure panel shows all 3 machine cards (Kush, Haze, APEX) with per-service up/down indicators.
4. APEX status accurately reflects whether port 8443 is reachable over HTTPS.
5. If the Leroy server is not running, the System tab shows a clear error message, not a black screen.
6. If brain is unreachable but infra responds (or vice versa), the working panel renders normally and the failed panel shows its error. No full-screen blank.
7. Loading state is visually obvious on the dark theme (not dark grey on dark background).
8. No changes to any other tab's behavior or appearance.

## Constraints

- React 18 + Vite + Tailwind (existing stack). No new dependencies.
- Match existing dark theme (forge-bg, forge-card, forge-border, forge-surface).
- Do not modify the tab routing mechanism in App.jsx.
- Do not modify any other tab components.
- Keep the existing SystemTab two-panel layout (Brain left, Infra right).
- AbortController for fetch timeout (native browser API, no polyfill needed).
- All server changes in server.py only.

## Do Not Do

- Do not redesign the System tab layout or add new sections.
- Do not add new server endpoints. Fix the existing ones.
- Do not add WebSocket or SSE to the System tab (polling is fine for now).
- Do not add authentication to the health endpoints.
- Do not modify any other files in the dashboard beyond useSystem.js and SystemTab.jsx.
- Do not add npm dependencies.

## Machine Details

- Dashboard: ~/Projects/leroy/dashboard/, Vite dev server on localhost:5173
- Server: ~/Projects/leroy/server/server.py, port 9800
- Brain proxy target: Kush 192.168.1.100:8300 (forge-brain), health on 8301
- APEX: 155.138.199.82:8443 (A2A Gateway, HTTPS)

## Files to Modify

1. `dashboard/src/hooks/useSystem.js` -- fetch error handling + timeout
2. `dashboard/src/components/tabs/SystemTab.jsx` -- loading state + error rendering
3. `server/server.py` -- APEX HTTPS ping + brain_health locals() fix

## Budget

Simple. Four targeted fixes across 3 files. No architectural changes.

## Execution

Use agent teams. Frontend and backend fixes are independent and can run in parallel.
---
## Outcome
**Task ID:** 748a1db6-d36a-4019-9a71-93873d82dc54
**QA pass rate:** 10/10

## Retrospective
## Retrospective
**What worked in this spec:** The four-bug decomposition was precise. Each fix was scoped to a specific file and specific behavior, which let Leroy execute all four in a single pass without ambiguity. Naming the exact files and the exact failure mode (e.g., "locals() not dir()" for brain_health, "https:// for port 8443" for APEX) eliminated guesswork. Build completed in ~2.5 minutes. The constraint to preserve Promise.allSettled prevented a common regression path.

**What caused friction:** None observed. Clean 10/10 QA pass with no rework. The spec was tight enough that even the QA spec's 10 test cases all passed on first run. The only thing that could have been better: the spec didn't explicitly call out the loading indicator color requirements (text-slate-400 vs dark theme), but the QA spec caught that as Test 7 and it passed anyway because the fix naturally used visible colors.

**Spec improvement for next time:** When fixing UI rendering bugs on dark themes, explicitly state minimum contrast requirements or specific Tailwind color classes in the build spec, not just the QA spec. The build happened to get it right, but the spec should have been explicit about "loading indicator must be visible against forge-bg" as a build requirement, not just a QA check.
