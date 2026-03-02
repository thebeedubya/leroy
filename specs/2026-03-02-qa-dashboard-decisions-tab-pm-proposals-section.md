---
spec_id: qa-dashboard-decisions-tab-pm-proposals-section
task_id: 39ce420b-8f45-44e7-94ce-475963f2a401
date: 2026-03-02
status: failed
pass_rate: 13/14 (1 failure: Test 14 spec compliance -- backend modified despite spec saying no backend changes)
retrospective: ## Retrospective **What worked in this spec:** 13 of 14 frontend tests passed cleanly. Component placement, empty state, card layout, approve/reject buttons, recently decided section, loading states, error handling, accessibility, theme compliance all verified. The QA spec was thorough and each criterion was testable. **What caused friction:** Test 14 failed because Leroy added backend /pm/proposals endpoints (4 routes + ProposalStore class in task_db.py), but the spec said "do not modify backend code." The backend endpoints were actually already live (built by Ops during headless PM infrastructure work). The QA spec's "no backend changes" constraint was correct, but the build spec for 5b22edb8 was under-scoped. It said frontend-only, but Leroy either didn't know the endpoints existed or added them defensively. **Spec improvement for next time:** When speccing frontend work that depends on backend endpoints, explicitly state "the following endpoints already exist and are live: [list]." Don't just say "no backend changes" without confirming the engineer knows the backend is already in place. The constraint was ambiguous from Leroy's perspective.
tags: []
---

# QA: Dashboard Decisions Tab -- PM Proposals Section

## Objective
Validate the build output of task 5b22edb8-612a-422a-be06-337b32796db7 against the original spec's success criteria. This is a frontend-only QA pass on the PM Proposals section added to the Decisions tab.

## Original Spec Reference
Task 5b22edb8. Added a PM Proposals section to the Decisions tab of the workforce hub dashboard. React 18 + Vite + Tailwind. Polling backend endpoints at localhost:9800. No backend modifications.

## Test Plan

### Test 1: Section Placement
**Criterion:** PM Proposals section appears ABOVE "Needs Your Response" in the Decisions tab.
**How to verify:** Read the Decisions tab component source. Confirm ProposalsSection is rendered before the existing messages/needs-response section in the JSX.
**Pass/Fail:** Binary.

### Test 2: Empty State Hidden
**Criterion:** Section is hidden when there are zero pending proposals.
**How to verify:** Read the component or hook logic. Confirm that when the pending proposals array is empty, the section does not render (conditional rendering or early return).
**Pass/Fail:** Binary.

### Test 3: Proposal Card Content
**Criterion:** Pending proposals display title, type badge (color-coded: QA Spec blue, Build Spec amber, Respec red), trigger event, reasoning, and time waiting.
**How to verify:** Read the proposal card component. Confirm each field is rendered. Confirm badge colors match spec (blue/amber/red by type). Confirm relative time display.
**Pass/Fail:** Binary.

### Test 4: Collapsible Spec Preview
**Criterion:** Spec preview is collapsible and shows full content when expanded.
**How to verify:** Read the component. Confirm there is a collapsible/expandable section for spec content. Confirm it initially shows truncated content (first ~20 lines) and expands to full on click. Confirm monospace font for spec content.
**Pass/Fail:** Binary.

### Test 5: Approve Flow
**Criterion:** Approve button sends POST /pm/proposals/{id}/approve and removes the card.
**How to verify:** Read the useProposals hook and the approve handler. Confirm it sends POST to the correct endpoint. Confirm the card is removed from the UI on success (either re-fetch or optimistic removal).
**Pass/Fail:** Binary.

### Test 6: Reject Requires Feedback
**Criterion:** Reject button requires feedback text before submitting.
**How to verify:** Read the reject UI logic. Confirm clicking reject opens an inline text input. Confirm the submit button is disabled or blocked when feedback is empty.
**Pass/Fail:** Binary.

### Test 7: Reject Flow
**Criterion:** Reject sends POST /pm/proposals/{id}/reject with feedback payload and removes the card.
**How to verify:** Read the reject handler. Confirm it sends POST to the correct endpoint with {feedback: "..."} in the body. Confirm card removal on success.
**Pass/Fail:** Binary.

### Test 8: Recently Decided Section
**Criterion:** Recently decided section shows last 5 resolved proposals, collapsed by default.
**How to verify:** Read the component. Confirm a "Recently decided" section exists. Confirm it polls GET /pm/proposals?status=all&limit=5. Confirm it is collapsed by default and expandable. Confirm it shows title, outcome, feedback (if rejected), and timestamp.
**Pass/Fail:** Binary.

### Test 9: Badge Count
**Criterion:** Badge count on section header matches pending proposal count.
**How to verify:** Read the section header rendering. Confirm there is a count badge that reflects the length of the pending proposals array.
**Pass/Fail:** Binary.

### Test 10: Polling Intervals
**Criterion:** Polling refreshes pending proposals every 10 seconds and recently decided every 30 seconds.
**How to verify:** Read the useProposals hook. Confirm setInterval or equivalent with 10000ms for pending and 30000ms for recently decided.
**Pass/Fail:** Binary.

### Test 11: Dark Theme Compliance
**Criterion:** Dark theme matches existing dashboard (forge-bg, forge-card, forge-border, forge-surface classes).
**How to verify:** Read the component JSX. Confirm it uses the same Tailwind classes and color tokens as the existing Decisions tab. No light-mode-only colors, no hardcoded colors outside the theme.
**Pass/Fail:** Binary.

### Test 12: No New Dependencies
**Criterion:** No new npm dependencies added.
**How to verify:** Check package.json for any new entries in dependencies or devDependencies compared to the pre-build state. If you cannot diff, read package.json and confirm no unexpected packages.
**Pass/Fail:** Binary.

### Test 13: Existing Content Unmodified
**Criterion:** The existing "Needs Your Response" messages section is not modified.
**How to verify:** Confirm the existing messages/needs-response rendering logic is unchanged. The new section is additive only.
**Pass/Fail:** Binary.

### Test 14: No Backend Modifications
**Criterion:** No backend code was modified.
**How to verify:** Confirm no changes to server.py or any backend files.
**Pass/Fail:** Binary.

## Constraints
- Do not modify any build output
- Test against the spec criteria above, not assumptions
- Report exact pass/fail for each criterion (14 total)
- Read source code to verify; do not require a running server

## Machine Details
- Dashboard source: ~/Projects/leroy/dashboard/ (React 18, Vite, Tailwind)
- Server source: ~/Projects/leroy/server/ (do not modify)
- The dashboard runs on port 5173, server on port 9800

## Execution
Use agent teams. One agent per logical group of tests. Report pass/fail for all 14 criteria.
---
## Outcome
**Task ID:** 39ce420b-8f45-44e7-94ce-475963f2a401
**QA pass rate:** 13/14 (1 failure: Test 14 spec compliance -- backend modified despite spec saying no backend changes)

## Retrospective
## Retrospective
**What worked in this spec:** 13 of 14 frontend tests passed cleanly. Component placement, empty state, card layout, approve/reject buttons, recently decided section, loading states, error handling, accessibility, theme compliance all verified. The QA spec was thorough and each criterion was testable.
**What caused friction:** Test 14 failed because Leroy added backend /pm/proposals endpoints (4 routes + ProposalStore class in task_db.py), but the spec said "do not modify backend code." The backend endpoints were actually already live (built by Ops during headless PM infrastructure work). The QA spec's "no backend changes" constraint was correct, but the build spec for 5b22edb8 was under-scoped. It said frontend-only, but Leroy either didn't know the endpoints existed or added them defensively.
**Spec improvement for next time:** When speccing frontend work that depends on backend endpoints, explicitly state "the following endpoints already exist and are live: [list]." Don't just say "no backend changes" without confirming the engineer knows the backend is already in place. The constraint was ambiguous from Leroy's perspective.
