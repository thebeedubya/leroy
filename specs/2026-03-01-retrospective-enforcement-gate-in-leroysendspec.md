---
spec_id: retrospective-enforcement-gate-in-leroysendspec
task_id: ff241ace-eb99-449f-862a-a393e9ef876e
date: 2026-03-01
status: completed
pass_rate: 7/7
retrospective: What worked: Perfect spec execution. Single file, 30 lines of new code, under 2 minutes. Spec was tight: exact function to modify, exact logic to add, exact error message format, explicit override mechanism. This is the PM tooling upgrade spec (1382c4e1) pattern repeated. Tight scope, single file, clear deliverables.  What caused friction: Nothing. Clean pass. The spec benefited from the retro I wrote on 1382c4e1 earlier this session, which said "this is the template to follow."  Spec improvement for next time: This is the gold standard for tooling specs. Keep repeating this pattern: modify one file, use existing helpers, explicit success criteria, explicit "do not do" list.
tags: []
---

# Retrospective Enforcement Gate in leroy_send_spec

## Objective

Add a pre-flight check to `leroy_send_spec()` in `mcp/leroy_client.py` that blocks new spec submission if any completed spec files are missing retrospectives. PM has a pattern of skipping retros and moving to the next task. The tooling should enforce the discipline, not rely on PM behavior.

## Why

PM completed 18 tasks across 11 spec files without writing a single retrospective until Brad caught it. The spec quality loop is broken without retros. This gate makes it mechanically impossible to send a new spec while retro debt exists.

## Scope

### In Scope
1. Pre-flight check in `leroy_send_spec()` before the A2A call
2. Scan spec files in `~/Projects/leroy/specs/` for completed tasks with `retrospective: (pending)`
3. Block submission and return a clear error listing which specs need retros
4. Allow an override flag for urgent specs (escape hatch, logged)

### Out of Scope
- No changes to `leroy_update_spec` (already works)
- No changes to A2A server
- No changes to dashboard
- No new MCP tools

## Implementation

In `leroy_send_spec()`, before the A2A call, add:

1. Call `_get_recent_spec_files(n=20)` (existing function)
2. For each file, parse front matter (existing `_parse_frontmatter`)
3. Check: if `status` is `completed` AND `retrospective` is `(pending)` or empty, it's retro debt
4. If retro debt exists, return an error message instead of sending the spec:

```
BLOCKED: {N} spec(s) missing retrospectives. Write retros before sending new specs.

Missing retros:
- 2026-03-01-content-agent-pipeline.md (task: 31fe79e5)
- 2026-03-01-fix-uuid-display.md (task: ba03d987)

Use leroy_update_spec(task_id, pass_rate, retrospective) to write each retro.
To override (emergency only): include "RETRO_OVERRIDE" in the spec subject.
```

5. If the spec subject contains "RETRO_OVERRIDE", skip the gate but log a warning in the response: "WARNING: Retro gate overridden. You still owe {N} retrospectives."

## Success Criteria

1. Sending a spec with pending retros returns a blocking error listing the delinquent specs
2. The error message includes spec filenames and task IDs for each missing retro
3. After writing all missing retros via `leroy_update_spec`, `leroy_send_spec` succeeds normally
4. Including "RETRO_OVERRIDE" in the subject bypasses the gate with a logged warning
5. Specs with status `sent`, `failed`, or `cancelled` do not trigger the gate (only `completed` with pending retro)
6. No changes to any file other than `mcp/leroy_client.py`
7. Existing `leroy_send_spec` behavior (A2A call, auto-save, retrospective injection) unchanged when gate passes

## Constraints

- Single file change: `mcp/leroy_client.py` only
- Use existing helper functions (`_get_recent_spec_files`, `_parse_frontmatter`)
- No new dependencies
- The gate check must complete in under 1 second (it's scanning local markdown files)
- Do not modify the A2A call path. The gate is a pre-flight check only.

## Do Not Do

- Do not add a database or persistent state for retro tracking (file scanning is sufficient)
- Do not modify `leroy_update_spec`
- Do not add a new MCP tool
- Do not modify the server
- Do not add async complexity (file scanning is synchronous and fast)

## Machine Details

- Haze: `~/Projects/leroy/mcp/leroy_client.py`
- Specs directory: `~/Projects/leroy/specs/`
- Python 3.14, FastMCP

## Budget

Simple. Single file, ~30 lines of new code. Under 10 minutes.

## Execution

Use agent teams. Decompose this into sub-tasks and delegate to specialist agents. Do not execute sequentially as a single agent.

---
## Outcome
**Task ID:** ff241ace-eb99-449f-862a-a393e9ef876e
**QA pass rate:** 7/7

## Retrospective
What worked: Perfect spec execution. Single file, 30 lines of new code, under 2 minutes. Spec was tight: exact function to modify, exact logic to add, exact error message format, explicit override mechanism. This is the PM tooling upgrade spec (1382c4e1) pattern repeated. Tight scope, single file, clear deliverables.

What caused friction: Nothing. Clean pass. The spec benefited from the retro I wrote on 1382c4e1 earlier this session, which said "this is the template to follow."

Spec improvement for next time: This is the gold standard for tooling specs. Keep repeating this pattern: modify one file, use existing helpers, explicit success criteria, explicit "do not do" list.
