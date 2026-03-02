---
spec_id: content-agent-dedicated-daily-media-pipeline-phase
task_id: 31fe79e5-744b-4ecc-9886-f70c51cf1f75
date: 2026-03-01
status: completed
pass_rate: 7/9 (2 BLOCKED: cannot test nested Claude session E2E, cannot verify Aianna persist in automated run)
retrospective: What worked: Spec was comprehensive. Agent architecture section (identity, launcher, tools, portability) gave Leroy a clear blueprint. Voice rules were specific enough to be actionable. Pipeline steps were numbered and ordered. Leroy built the full agent in 7 minutes across 6 deliverables. The portability requirement (no local state, brain is network, output is GitHub) was a good constraint that shaped the design well.  What caused friction: Two success criteria were untestable by Leroy (nested Claude session restriction). I should have known that Leroy can't spawn another Claude instance to E2E test an agent launcher. Also, the DST note about launchd Hour=12 UTC shifting from 6AM CST to 7AM CDT was caught by Leroy, not by me. I should have specified UTC-aware scheduling or flagged the DST edge case in the spec.  Spec improvement for next time: For agent-building specs, separate "build" criteria (files exist, syntax valid, config correct) from "runtime" criteria (actually runs end-to-end). Mark runtime criteria as "manual verification required" so they don't show as failures. Also, always specify timezone handling explicitly when scheduling is involved.
tags: []
---

# Content Agent -- Dedicated Daily Media Pipeline

## Objective
Build a dedicated, portable content agent that autonomously mines Aianna for yesterday's FORGE activity, generates multi-platform content drafts (blog + LinkedIn + X + Instagram), and opens a GitHub PR on dbradwood.com for Brad's approval. On merge, the agent triggers social media posting via Cowork.

## Why
Brad's personal brand has been dark since Feb 8. The raw material exists -- Aianna captures every decision, architecture breakthrough, emotional arc, and lesson learned. The gap is a pipeline that transforms brain activity into publishable content without manual effort beyond a morning approval pass.

## Agent Architecture

### Identity
- **Name:** Content Agent
- **Launcher:** `./content.sh` (new, follows pm.sh/leroy.sh/ops.sh pattern)
- **Persona:** `personas/content_agent.md` (new)
- **Tools needed:** Bash, Read, Write, Glob, Grep, forge-brain MCP, GitHub CLI (`gh`)
- **Tools NOT needed:** Edit, SSH, A2A, Leroy MCP (this agent doesn't manage tasks)
- **Settings:** `.claude/content-settings.json` (new)

### Portability
The agent's brain is Aianna (network accessible). Its output target is a GitHub repo. It can run from any machine with:
1. Claude Code installed
2. forge-brain MCP access (Kush:8300)
3. GitHub CLI authenticated (`gh auth`)
4. Git configured with push access to thebeedubya/dbradwood.com

No local state files. No machine-specific paths. Move it anywhere.

## Daily Pipeline (6 AM trigger)

### Step 1: Query Aianna
Six parallel `query_memory` calls covering:
- Milestones, completions, deployments
- Architecture decisions with reasoning
- Cross-agent activity (Leroy specs, A2A messages)
- Emotional high points (excitement, frustration arcs)
- Brad building/operating narrative
- Problems solved, lessons learned

Plus `query_lessons` (recent) and `get_forge_state` for current context.

### Step 2: Filter to Yesterday
Filter all results to 24-hour window. Deduplicate by session_id.
If zero results: write "nothing compelling" log entry and stop. No PR.

### Step 3: Score Content Angles
Scoring rubric:

**Positive:**
- +3: Shipped/deployed/validated something working
- +3: Architecture decision with clear reasoning
- +2: Lesson learned (wrong approach to correct approach)
- +2: Cross-agent event
- +2: Emotional arc (frustration to resolution)
- +2: Novel insight about AI/memory/agents
- +1: Significant decision with tradeoffs
- +1: FORGE ecosystem progress

**Negative:**
- -2: Pure config, no insight
- -2: Routine debugging, no resolution
- -2: Mundane back-and-forth
- -1: Repetitive topic

**Threshold:** >= 3 is post-worthy. Cap at top 3 angles.

### Step 4: Generate Content
For each angle that clears threshold:

**Blog Post (dbradwood.com)**
- MDX format, 500-800 words
- Frontmatter must pass zod validation in `src/lib/contentfs.ts`:
  - `type: writing` (literal, required)
  - `title` (string, min 1 char, required)
  - `summary` (string, min 1 char, required)
  - `status: draft` (literal, required -- NEVER set to published)
  - `publishedAt: YYYY-MM-DD` (optional but always include)
  - `tags` (string array, optional)
- File: `content/writing/{slug}.mdx`
- Slug: lowercase, hyphenated, max 80 chars (see slugify() in scripts/ingest.mjs for reference)
- Can use MDX components: `<Callout>`, `<Checklist>`, `<MetricRow>`
- Ends with takeaway, not CTA
- Reference existing posts for style: `content/writing/revenue-reporting-you-can-certify-to-the-penny.mdx`, `content/writing/democracy-has-a-supply-chain.mdx`

**LinkedIn**
- 1000-1300 chars
- First line is hook (no "I" start)
- 4-6 short paragraphs
- Ends with discussion question
- 3-5 hashtags at end only

**X Thread**
- Hook tweet + 3-5 follow-ups + landing tweet
- Each under 240 chars, self-contained
- 1-2 hashtags max per tweet
- Numbered (1/, 2/...)

**Instagram**
- 150-200 word caption
- 15-20 hashtags
- Carousel outline (5-7 slides, slide 1 = hook, last = CTA + @dbradwood)

### Step 5: Open PR
1. Clone or pull `thebeedubya/dbradwood.com` from GitHub
2. Create branch: `content/YYYY-MM-DD`
3. Commit blog MDX file(s) to `content/writing/{slug}.mdx`
4. Commit social drafts to `content/social/YYYY-MM-DD.md` (new directory)
5. Push branch
6. Open PR against `main` via `gh pr create`
   - Title: "Daily Content: YYYY-MM-DD"
   - Body: summary of angles, scoring rationale, platform breakdown
7. Persist PR URL to Aianna via `persist_append`

### Step 6: Approval Gate
Brad reviews PR on GitHub (phone or desktop). He can:
- Edit any draft directly in the PR
- Delete content he doesn't want
- Change `status: draft` to `status: published` for blog posts he wants live
- Merge when ready -- Vercel auto-deploys

## Voice Rules (baked into persona)
- First person, Brad's voice
- No emojis except IG
- No "I'm excited to announce" or "thrilled to share"
- No "game-changer," no em dashes, no "genuinely," "honestly," "straightforward"
- Contrarian angles welcome
- Technical + credible + accessible to non-engineers
- Operator talking about what he built and why

## Scheduling
- macOS launchd plist: `com.forge.content-agent.plist`
- Install to: ~/Library/LaunchAgents/
- Trigger: daily at 6:00 AM CST
- Runs: `claude -p` with the content_agent.md persona and content-settings.json
- Working directory: ~/Projects/leroy/
- Stdout/stderr to: ~/Projects/leroy/content/logs/content-agent-YYYY-MM-DD.log

## Scope

### In Scope (Phase 1)
1. Create `personas/content_agent.md` with full pipeline instructions baked in
2. Create `content.sh` launcher script
3. Create `.claude/content-settings.json` with appropriate tool permissions
4. Create `com.forge.content-agent.plist` launchd schedule
5. Ensure `gh` CLI is authenticated and can push to thebeedubya/dbradwood.com
6. Create `content/social/` directory in dbradwood.com repo
7. Test end-to-end: run the agent manually, verify PR appears on GitHub with valid MDX + social drafts

### Out of Scope
- Automated social media posting (Phase 2 -- Cowork integration)
- Merge detection / webhook (Phase 2)
- Modifying existing blog posts
- Dashboard integration
- Any changes to PM, Leroy, or Ops agents

## Success Criteria
1. Agent runs via `./content.sh` without errors
2. Agent queries Aianna and scores content angles correctly
3. If post-worthy content exists, a PR appears on thebeedubya/dbradwood.com
4. PR contains properly formatted MDX blog post(s) that pass zod frontmatter validation
5. PR contains social media drafts for LinkedIn, X, and Instagram in `content/social/`
6. Brad can review, edit, and merge the PR from GitHub mobile
7. Merging the PR auto-deploys blog content via Vercel (existing behavior, just verify)
8. LaunchD plist triggers the agent at 6 AM daily
9. All activity persisted to Aianna

## Constraints
- Do NOT auto-publish anything. Everything goes through the PR approval gate.
- Do NOT store credentials in code. Use `gh auth` and git credential helpers.
- Do NOT modify existing blog posts or content. Only create new files.
- Clone dbradwood.com from GitHub (https://github.com/thebeedubya/dbradwood.com.git), not via SSH to Kush.
- Blog posts MUST commit as `status: draft`. Brad manually promotes to `published`.
- Read the existing daily-media command at `.claude/commands/daily-media.md` for reference -- it has the Aianna query patterns and scoring rubric. Reuse what works, but this is a standalone agent, not a slash command.
- Read existing blog posts in the dbradwood.com repo to match Brad's writing style and MDX conventions.

## Machine Details
- Haze (primary): ~/Projects/leroy/ (agent home), clone dbradwood.com to ~/Projects/dbradwood.com
- Kush (192.168.1.100): forge-brain at port 8300/8301
- GitHub repo: https://github.com/thebeedubya/dbradwood.com.git
- Git user: thebeedubya (verify with `gh auth status`)

## Execution
Use agent teams. Decompose this into sub-tasks and delegate to specialist agents. Do not execute sequentially as a single agent.
---
## Outcome
**Task ID:** 31fe79e5-744b-4ecc-9886-f70c51cf1f75
**QA pass rate:** 7/9 (2 BLOCKED: cannot test nested Claude session E2E, cannot verify Aianna persist in automated run)

## Retrospective
What worked: Spec was comprehensive. Agent architecture section (identity, launcher, tools, portability) gave Leroy a clear blueprint. Voice rules were specific enough to be actionable. Pipeline steps were numbered and ordered. Leroy built the full agent in 7 minutes across 6 deliverables. The portability requirement (no local state, brain is network, output is GitHub) was a good constraint that shaped the design well.

What caused friction: Two success criteria were untestable by Leroy (nested Claude session restriction). I should have known that Leroy can't spawn another Claude instance to E2E test an agent launcher. Also, the DST note about launchd Hour=12 UTC shifting from 6AM CST to 7AM CDT was caught by Leroy, not by me. I should have specified UTC-aware scheduling or flagged the DST edge case in the spec.

Spec improvement for next time: For agent-building specs, separate "build" criteria (files exist, syntax valid, config correct) from "runtime" criteria (actually runs end-to-end). Mark runtime criteria as "manual verification required" so they don't show as failures. Also, always specify timezone handling explicitly when scheduling is involved.
