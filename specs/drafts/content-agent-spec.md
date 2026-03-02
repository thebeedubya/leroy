# Content Agent -- Dedicated Daily Media Pipeline

## Objective
Build a dedicated, portable content agent that autonomously mines Aianna for yesterday's FORGE activity, generates multi-platform content drafts (blog + LinkedIn + X + Instagram), and opens a GitHub PR on dbradwood.com for Brad's approval. On merge, the agent triggers social media posting via Cowork.

## Why
Brad's personal brand has been dark since Feb 8. The raw material exists -- Aianna captures every decision, architecture breakthrough, emotional arc, and lesson learned. The gap is a pipeline that transforms brain activity into publishable content without manual effort beyond a morning approval pass.

## Agent Architecture

### Identity
- **Name:** Content Agent (or "Scribe" -- TBD)
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
Scoring rubric (from existing daily-media command):

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
- Frontmatter: `type: writing`, `title`, `summary`, `status: draft`, `publishedAt: YYYY-MM-DD`, `tags`
- File: `content/writing/{slug}.mdx`
- Slug: lowercase, hyphenated, max 80 chars
- Can use MDX components: `<Callout>`, `<Checklist>`, `<MetricRow>`
- Ends with takeaway, not CTA

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
1. Clone or pull `thebeedubya/dbradwood.com` (GitHub, HTTPS)
2. Create branch: `content/YYYY-MM-DD`
3. Commit blog MDX file(s) to `content/writing/{slug}.mdx`
4. Commit social drafts to `content/social/YYYY-MM-DD.md` (new directory)
5. Push branch
6. Open PR against `main` via `gh pr create`
   - Title: "Daily Content: YYYY-MM-DD"
   - Body: summary of angles, scoring rationale, platform breakdown
7. Log PR URL to Aianna

### Step 6: Wait for Merge (Approval Gate)
Brad reviews PR on GitHub (phone or desktop). He can:
- Edit any draft directly in the PR
- Delete content he doesn't want
- Change `status: draft` to `status: published` for blog posts he wants live
- Merge when ready

### Step 7: Post-Merge Social Publishing
On merge detection (polling or webhook):
1. Read merged social drafts from `content/social/YYYY-MM-DD.md`
2. Trigger Cowork (`post_to_platforms.py`) for approved platforms
3. Log posting results to Aianna
4. Clean up: delete the social file from repo (it served its purpose)

**Note:** This step is Phase 2. MVP is Steps 1-6.

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
- Trigger: daily at 6:00 AM CST
- Runs: `claude -p "$(cat personas/content_agent.md)" --settings .claude/content-settings.json`
- Working directory: ~/Projects/leroy/
- Log: ~/Projects/leroy/content/logs/content-agent-YYYY-MM-DD.log

## File Inventory (to create)
1. `personas/content_agent.md` -- agent persona + full pipeline instructions
2. `content.sh` -- launcher script
3. `.claude/content-settings.json` -- tool permissions
4. `com.forge.content-agent.plist` -- launchd schedule
5. Directory: `content/social/` in dbradwood.com repo (for social draft staging)

## Success Criteria
1. Agent runs autonomously at 6 AM daily without human trigger
2. If Aianna has post-worthy content from yesterday, a PR appears on dbradwood.com
3. PR contains properly formatted MDX blog post(s) with valid frontmatter
4. PR contains social media drafts for LinkedIn, X, and Instagram
5. Brad can review, edit, and merge the PR from his phone
6. Merging the PR auto-deploys blog content via Vercel
7. All activity persisted to Aianna for future context

## Constraints
- Do NOT auto-publish anything. Everything goes through the PR approval gate.
- Do NOT store credentials in code. Use `gh auth` and git credential helpers.
- Do NOT modify existing blog posts. Only create new content.
- The dbradwood.com repo must be cloned from GitHub, not accessed via SSH to Kush.
- Blog posts commit as `status: draft`. Brad manually promotes to `published` when ready.

## Phasing
- **Phase 1 (this spec):** Steps 1-6. Agent generates content and opens PR. Manual social posting.
- **Phase 2 (future spec):** Step 7. Merge detection + automated Cowork posting.

## Execution
Use agent teams. Decompose this into sub-tasks and delegate to specialist agents. Do not execute sequentially as a single agent.
