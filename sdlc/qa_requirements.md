# QA Requirements

QA is not optional. Every micro-sprint includes a QA agent.

## Test Framework Stack

### pytest (Unit/Integration)
- API endpoint validation
- Business logic correctness
- Data pipeline integrity
- Edge cases and error handling
- Configuration validation
- MCP tool response verification

### Playwright (E2E Browser)
- Navigation flows
- UI state management
- Form submission and validation
- Visual regression (screenshot comparison)
- Cross-browser when applicable

## Test-First Principle

The QA agent writes tests FROM THE SPEC before seeing any build output.

Tests define "done." If the build passes the tests, it's done. If not, it's not. No subjective evaluation. No "looks good to me."

This means:
1. QA agent receives the spec (same spec the builder received)
2. QA writes test suite based on success criteria
3. Only then does QA receive the build output
4. Tests run against the build
5. Results are binary: pass or fail

## Multi-Candidate Scoring

When the DisCIPL pattern is used (2-3 candidates):

1. QA writes test suite from spec (once, before seeing any candidate)
2. All candidates tested against the same suite
3. Evaluator uses QA results as primary scoring input:
   - QA pass rate (weighted highest)
   - Code quality (readability, patterns, structure)
   - Approach elegance (simplicity, maintainability)
   - Performance (if applicable)
4. Ties broken by maintainability

## Rework Rules

- QA fails -> specific failures reported with reproduction steps
- Builder fixes (or fresh builder spawns with failure context)
- QA reruns full suite (not just failed tests)
- Max 3 rework cycles per sprint
- If 3 cycles fail: escalate to Engineering Lead
- Lead can re-decompose, reassign, or escalate to PM

## What QA Does NOT Do

- QA does not write application code
- QA does not suggest implementation changes (only reports failures)
- QA does not subjectively evaluate code quality (that's the Evaluator's job)
- QA does not skip tests because "it looks right"
- QA does not approve with caveats ("passes except for..."). It passes or it doesn't.

## Infrastructure Testing

For any deployment-related sprint:
- Verify service health post-deploy (sentinel query)
- Verify no regression in existing services
- Verify rollback procedure works (dry run when possible)
- Verify monitoring/alerting catches the new service
