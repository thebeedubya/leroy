# Escalation Rules

## Three-Tier Escalation Chain

Every level has a cap. Nobody loops forever.

## Tier 1: Workforce Agent -> Engineering Lead

**Trigger**: Agent tries 3 approaches to the same problem and none work.

**Action**: STOP immediately. Report to Engineering Lead:
- What was tried (all 3 approaches, specifically)
- What each attempt produced (error messages, silent failures, partial results)
- What the agent thinks the blocker is (theory, if it has one)

The agent does NOT keep trying variations of the same failed approach. Three strikes and it stops.

Note: This is different from the QA rework loop (SDLC Step 7). The rework loop is: build exists, QA found a bug, fix and retest. The stuck protocol is: agent can't get the thing working at all.

**Lead options**:
- Provide new context or hints (query brain for relevant lessons)
- Reassign to a fresh agent with different instructions
- Escalate to PM for architectural guidance
- Kill the approach and propose alternative to PM

## Tier 2: Engineering Lead -> PM

**Trigger**: 3 rework cycles exhausted, or workforce agents stuck on a problem the Lead can't resolve.

**Action**: STOP the sprint. Report to PM:
- What was attempted across all agents
- Where it's failing and why
- What options remain
- Budget consumed so far

**PM options**:
- Redirect approach
- Provide missing context
- Approve scope change
- Kill the sprint

## Tier 3: PM -> Operator (Brad)

**Trigger**: PM lacks context or authority to resolve. External dependency, business decision, architectural pivot beyond spec scope.

**Action**: PM presents situation to Brad with:
- Clear problem statement
- Options evaluated
- PM's recommendation
- What's needed from Brad to proceed

**Brad options**:
- Make the call
- Provide missing context
- Approve the pivot
- Table the work

## The Rule

**Three attempts at any level, then escalate up. No level loops forever. The cost of escalating is always less than the cost of grinding.**

## Context Pressure Protocol

If any agent detects context window pressure (approaching token limits during a long build):
1. Persist current state to brain immediately
2. Complete the current task
3. Report to Engineering Lead that context is tight
4. Lead spawns a fresh agent to continue with persisted state as input

Context pressure is not a failure. It's a resource constraint. Handle it cleanly.
