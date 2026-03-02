# Daily Media Cycle

Generate platform-specific social media content drafts from yesterday's Aianna session activity.

## What This Does

Queries forge-brain for the previous day's session data, identifies post-worthy moments, and generates drafts for dbradwood.com (blog), LinkedIn, X (thread), and Instagram. All content is Brad's voice, operator-grade, targeting CCO/AI Strategy/Board audience.

Output: `~/Projects/leroy/content/drafts/YYYY-MM-DD.md` (yesterday's date)

---

## Execution Instructions

Follow these steps in order. Do not skip steps. Do not generate content before completing the analysis phase.

### Step 1: Establish the Target Date Window

Determine yesterday's date. Today is the current date from your system context.

- Yesterday = today minus 1 day
- Time window = yesterday 00:00:00 UTC through yesterday 23:59:59 UTC
- Output filename = `~/Projects/leroy/content/drafts/YYYY-MM-DD.md` where YYYY-MM-DD is yesterday's date

### Step 2: Query Aianna — Cast a Wide Net

Execute ALL of these queries. Do not skip any. Aianna may be slow if MLX classification is running on Kush — retry up to 3 times on timeout before logging the failure and continuing.

Run these `query_memory` calls with `max_results: 8` each:

1. **Milestones and completions**: query = `"milestone breakthrough completed shipped deployed working validated success"`
2. **Architecture decisions**: query = `"architecture decision chosen approach designed built system reasoning trade-off"`
3. **Cross-agent activity**: query = `"Leroy task A2A agent orchestration Codex forge-brain Sentinel spec delivered"`
4. **Emotional high points**: query = `"excited frustrated blocked stuck finally working breakthrough relief"`
5. **Brad building narrative**: query = `"Brad FORGE Aianna memory ecosystem operator building daily"`
6. **Problems solved**: query = `"fixed resolved solved root cause discovered learned mistake error"`

Also run `query_lessons` with no filter and `limit: 10` to capture recent lessons.

Also run `get_forge_state` to capture current project status.

### Step 3: Filter to Yesterday's Window

From all query results combined, filter to sessions with timestamps within the 24-hour window of yesterday (UTC). Use the `timestamp` field on each result.

Deduplicate by `session_id`. Keep unique sessions only.

If zero sessions are found for yesterday's window:
- Write the output file with a brief "nothing compelling today" note (see format below)
- Stop. Do not force content.

### Step 4: Score Each Session for Post-Worthiness

For each unique session from yesterday, assign a score based on these signals. Score each session, not each chunk.

**Positive signals:**
- +3: Shipped, deployed, or validated something working
- +3: Architecture decision with clear reasoning (chose X because Y, rejected Z because W)
- +2: Lesson learned — something went wrong, root cause found, correct approach identified
- +2: Cross-agent event (Leroy executed a spec, A2A message sent/received, Codex involved)
- +2: Emotional arc — frustration followed by resolution, or clear excitement
- +2: Novel insight about AI, memory, or agent orchestration
- +1: Significant decision made with tradeoffs documented
- +1: FORGE ecosystem progress (Aianna, Sentinel, Leroy, A2A)

**Negative signals (subtract):**
- -2: Pure config tweaking with no insight
- -2: Routine debugging with no resolution or lesson
- -2: Mundane back-and-forth with no outcome
- -1: Repetitive topic already covered in a recent session

**Threshold**: Score >= 3 is post-worthy. Score < 3 is skip.

Rank the post-worthy sessions by score descending. Cap at 3 content angles (avoid overwhelming Brad with drafts). If more than 3 score above threshold, take the top 3.

### Step 5: Synthesize Content Angles

For each post-worthy session (up to 3), identify the core angle:

- What happened? (the event)
- Why does it matter? (the insight or implication)
- What's the contrarian or unexpected element? (the hook)
- Who is the target reader for this angle? (exec, AI builder, founder, etc.)
- What's the one-line thesis?

Write these angle summaries internally before generating drafts. They anchor the content.

### Step 6: Generate All Four Drafts Per Angle

For each content angle, generate all four drafts. Follow the platform guidelines exactly.

**Voice rules that apply to ALL platforms:**
- First person, Brad's voice. Direct. No corporate speak.
- An operator who builds things talking about what he built and why.
- Technical enough to be credible. Accessible enough for non-engineers.
- Confident, not boastful. Let the work speak.
- No emojis (IG is the only exception, and only if it genuinely fits).
- No "I'm excited to announce." No "thrilled to share." No "game-changer." No em dashes.
- No "genuinely," "honestly," or "straightforward."
- Contrarian angles welcome.

---

#### Draft 1: dbradwood.com Blog Post

Length: 500-800 words
Format: Full markdown with H1, headers, code/architecture callouts where relevant

Required elements:
- `title:` (H1, punchy, SEO-optimized)
- `meta_description:` (150-160 chars, includes primary keyword)
- `seo_keywords:` (5-7 comma-separated)
- `slug:` (URL-friendly, hyphenated)
- Body with 3-4 sections using H2 headers
- At least one concrete technical detail, architecture decision, or code-adjacent insight
- Ends with a clear takeaway or lesson (not a call to action)
- Tone: operator's notebook. This is what I built and what I learned.

Structure:
```
---
title: [H1 title]
slug: [url-slug]
meta_description: [150-160 char description]
seo_keywords: [keyword1, keyword2, keyword3, keyword4, keyword5]
date: [yesterday's date YYYY-MM-DD]
source_sessions: [comma-separated session IDs]
---

[Body content in markdown]
```

---

#### Draft 2: LinkedIn

Length: 1000-1300 characters (count carefully)
Format: Line breaks for readability. Short paragraphs, 1-3 lines each.

Required elements:
- First line is the hook. Pattern interrupt, bold claim, or question. Must stand alone.
- Do NOT start with "I". Start with the topic, the observation, or the claim.
- Build the narrative through 4-6 short paragraphs
- End with a question or prompt for discussion (not "drop a comment")
- 3-5 hashtags at the very end. No hashtags in the body.
- No tagging other accounts (leave as placeholders if relevant)

Target reader: executives, hiring managers, AI strategists, board members.

---

#### Draft 3: X Thread

Format: Numbered tweets. Each tweet is self-contained but builds the narrative.

Tweet 1 (hook): Must stand alone as a compelling statement. Under 240 chars. Sets up the thread.
Tweets 2-5 (body): Each advances one idea. Concrete, specific, not vague.
Final tweet: Landing point. Takeaway or implication. Can include a link placeholder for the blog post.

Rules:
- Max 1-2 hashtags per tweet. Don't force them.
- Keep each tweet under 240 characters.
- Technical but accessible. No jargon without brief explanation.
- Tag people/projects only where specifically relevant (leave as placeholder).

Format:
```
1/ [hook tweet]

2/ [second beat]

3/ [third beat]

4/ [fourth beat]

5/ [fifth beat — optional]

6/ [landing tweet + blog link placeholder]
```

---

#### Draft 4: Instagram

Caption length: 150-200 words
Carousel: 5-7 slides outlined

Caption format:
- Opens with a story or scene-setting sentence
- Narrative arc through 3-4 short paragraphs
- Ends with a CTA (link in bio, save this post, etc.)
- 15-20 hashtags below the caption (mix of broad and niche AI/founder/builder tags)
- Emojis are acceptable here, use sparingly

Carousel slide outline:
- Slide 1: Hook/title (big bold claim or question)
- Slides 2-5 (or 6): One key point per slide, with supporting sentence
- Last slide: CTA + handle

Format:
```
CAPTION:
[caption text]

[hashtags]

CAROUSEL:
Slide 1: [hook/title text]
Slide 2: [point 1 heading] — [supporting line]
Slide 3: [point 2 heading] — [supporting line]
...
Last Slide: [CTA] — @dbradwood | dbradwood.com
```

---

### Step 7: Write the Output File

Write everything to `~/Projects/leroy/content/drafts/YYYY-MM-DD.md` (yesterday's date).

Use this exact structure:

```markdown
# Daily Media Brief: YYYY-MM-DD

## Yesterday's Summary

[2-3 sentences. What happened yesterday that was compelling. Honest assessment.]

**Content angles found**: N
**Sessions analyzed**: N
**Aianna queries run**: 6 (+ lessons + state)

---

## Content Angle 1: [Theme Name]

**Post-Worthiness Score**: X/10
**Target Angle**: [one-line thesis]
**Source Sessions**: [session IDs]
**Aianna Confidence**: [high / medium / low — based on data richness]
**Status:** draft
**Posted URLs:**

### Blog Post (dbradwood.com)

[full blog draft]

---

### LinkedIn

[full LinkedIn draft]

---

### X Thread

[full X thread]

---

### Instagram

[full Instagram draft]

---

[Repeat for Angle 2 and Angle 3 if applicable]

---

## Aianna Query Log

| Query | Results Returned | Yesterday's Sessions Found |
|-------|-----------------|---------------------------|
| Milestones/completions | N | N |
| Architecture decisions | N | N |
| Cross-agent activity | N | N |
| Emotional high points | N | N |
| Brad building narrative | N | N |
| Problems solved | N | N |
| Lessons (query_lessons) | N | N |

**Forge State loaded**: yes/no
**Aianna errors/retries**: [none / describe any]
```

---

### Nothing Compelling Format

If no sessions found in yesterday's window, write this and stop:

```markdown
# Daily Media Brief: YYYY-MM-DD

## Yesterday's Summary

No post-worthy sessions found in the 24-hour window for YYYY-MM-DD.

**Sessions analyzed**: 0
**Aianna queries run**: 6

Possible reasons:
- No active FORGE sessions yesterday
- Sessions are below the post-worthiness threshold (score < 3)
- Aianna query returned no results for the time window

No content drafted. Nothing forced.

---

## Aianna Query Log

[query log table]
```

---

## Quality Check Before Writing

Before writing the file, ask yourself:

1. Does each draft sound like Brad? (operator, direct, no fluff)
2. Does the hook actually hook? (would you stop scrolling?)
3. Is the technical detail specific enough to be credible?
4. Does the blog post have a real takeaway, not just a summary?
5. Is the LinkedIn under 1300 characters?
6. Is each X tweet under 240 characters?
7. Does the Instagram caption have hashtags?

If any answer is no, fix it before writing.

---

## Notes for Execution

- forge-brain is on Kush (192.168.1.100:8300). It can be slow. Retry up to 3x on timeout.
- All content is Brad's voice. You know Brad's story: CCO at Addium, building FORGE as an AI agent ecosystem, targeting CCO/Board/AI Strategy roles, named Aianna after his daughter Ayanna.
- The work is real. Don't editorialize it into something it's not. If a day was mostly config work, say so and generate nothing.
- Output path: `~/Projects/leroy/content/drafts/YYYY-MM-DD.md`
- Create the directory if it doesn't exist: `~/Projects/leroy/content/drafts/`
