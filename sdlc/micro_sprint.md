# Micro-Sprint SDLC Template

Every task Leroy executes follows this 15-step cycle. Steps scale to task complexity (a config change takes 10 minutes, a full build takes hours) but all steps are present.

## Phase 1: Planning

### Step 1 -- Spec Intake
- Receive spec from PM
- Validate: requirements clear? Success criteria defined and testable? Constraints listed? Machine details provided?
- If anything missing or ambiguous: ask PM BEFORE proceeding
- Do not guess. Do not assume.

### Step 2 -- Pre-flight Check
- Query sentinel for infrastructure health
- Verify SSH connectivity to target machines
- Confirm required tools/libraries available
- Query brain: `check_before_act` for relevant lessons
- If dependencies down: escalate to PM immediately

### Step 3 -- Research/Spike
- Only if unknowns exist (marked "research required" or discovered during intake)
- Investigate before building
- Document: what works, what doesn't, why
- If research reveals spec approach won't work: propose alternative to PM
- Skip if implementation path is clear

## Phase 2: Execution

### Step 4 -- Decomposition
- Break spec into tasks
- Assign to workforce agents with clear scope and file boundaries
- Identify parallel vs sequential work
- QA agent is MANDATORY in every decomposition
- For critical deliverables: spawn 2-3 candidates (DisCIPL pattern)

### Step 5 -- Build
- Workforce agents execute assigned tasks
- Parallel execution where no file overlap
- Max 3 tasks per agent, then die with fresh context
- Agents never talk to each other. Only Lead sees all results.
- Multi-candidate: 2-3 builders produce independent solutions

### Step 6 -- QA
- QA agent writes tests FROM THE SPEC before seeing build output
- pytest: unit tests, integration tests, API validation, edge cases
- Playwright: E2E browser tests, navigation, UI state, screenshots
- Multi-candidate: QA runs full suite against ALL candidates
- Results are the objective scoring function

### Step 7 -- Rework Loop
- QA fails -> QA reports specific failures with reproduction steps
- Builder fixes (or new builder spawns with failure context)
- QA reruns against fixes
- Max 3 rework cycles
- If 3 cycles fail: escalate to Lead for re-decomposition

### Step 8 -- Evaluator (Multi-Candidate Only)
- Score based on: QA pass rate, code quality, approach elegance, maintainability
- Winner selected with documented reasoning
- Losing candidates discarded (approach notes preserved for lessons)

## Phase 3: Review

### Step 9 -- Security/Lint
- No leaked tokens, keys, or secrets in code or logs
- File permissions correct (600 on keys, 644 on certs)
- Sensitive files in .gitignore
- No exposed ports beyond spec
- Dependency check for known vulnerabilities

### Step 10 -- Acceptance Criteria Traceability
- List every success criterion from spec
- Mark pass/fail with evidence for each
- This is what PM reviews. Not a narrative. A checklist.

### Step 11 -- PM Review
Send to PM:
- Acceptance criteria traceability
- Budget report (tokens, spawns, rework cycles, time)
- Deviations from spec with reasoning
- Rollback plan

PM response: APPROVE (proceed to deploy) | SEND BACK (specific items to address) | REJECT (back to spec phase)

## Phase 4: Ship

### Step 12 -- Rollback Plan
- Document before deployment
- What to revert if deployment fails
- How to verify revert worked
- Must be executable by Brad manually if needed

### Step 13 -- Deploy
- One step at a time
- Verify each step before next
- If any step fails: execute rollback, escalate to PM

### Step 14 -- Verify
- Run verification in production
- All success criteria confirmed live
- Health checks passing
- No regression in existing functionality

### Step 14.5 -- Document (NBL)
- Generate a narrative summary of the sprint: what was built, why, key decisions, what changed, architectural impact
- NOT raw code — a human-readable explanation suitable for team consumption
- Push to the project's internal notebook via `notebooklm_add_text` MCP tool
- If no project notebook exists, create one via `notebooklm_create_notebook`: `[Project Name] Internal`
- Title format: `Sprint: [subject] - [date]`
- Content should explain the "what and why" without exposing sensitive implementation details
- Include: problem statement, approach chosen, key files modified, architectural decisions, testing results

### Step 15 -- Persist
- Record outcomes to brain (what was built, why, decisions made)
- Record lessons learned (record_lesson for problems/solutions)
- Update FORGE-STATE if infrastructure changed
- Preserve budget report for historical tracking
