---
spec_id: dbradwoodcom-repo-recon
task_id: 3ebe0b93-182b-4fb4-8d35-5390ab95d112
date: 2026-03-01
status: completed
pass_rate: 3/4 (incomplete: commands were loosely specified, output was partial)
retrospective: What worked: Recon completed and returned useful data about the dbradwood.com repo.  What caused friction: The spec was too loose. "Explore the repo" is not a spec. I respecced it as 39471a51 with 14 numbered commands and got a much better result. The first attempt wasted a task slot.  Spec improvement for next time: Even recon tasks need structure. Number the commands. Specify expected output format. Don't say "explore" when you mean "run these 14 specific commands and return verbatim output."
tags: []
---

# dbradwood.com Repo Recon

## Objective
Explore the dbradwood.com Next.js repository on Kush to document the content structure, deployment pipeline, and git configuration. This intel is needed to spec a dedicated content agent that will autonomously generate blog posts and open PRs for approval.

## Scope

### In Scope
- Examine the repo at `~/Projects/dbradwood.com` on Kush (192.168.1.100)
- Document the Next.js content/blog structure:
  - Where do blog posts / writing entries live? (e.g., `content/`, `posts/`, `app/writing/`, MDX files?)
  - What frontmatter format do existing posts use? (title, date, slug, description, tags, etc.)
  - Copy the frontmatter from 2-3 existing posts verbatim
- Document the Vercel deployment setup:
  - Is there a `vercel.json`? If so, copy its contents
  - Is the Vercel CLI installed? (`which vercel`, `vercel --version`)
  - Is there a `.vercel/` directory with project config?
  - What's the deploy method: git push auto-deploy, Vercel CLI, or manual?
  - Check for any build scripts in package.json related to deployment
- Document git configuration:
  - `git remote -v` output
  - Current branch and any branch protection
  - Recent commit history (last 5 commits: `git log --oneline -5`)
- Document the project structure:
  - Top-level directory listing (`ls -la`)
  - Key config files: `next.config.js` or `next.config.mjs`, `package.json` (just the scripts and dependencies sections)
  - Any CMS integration (headless CMS, markdown pipeline, etc.)

### Out of Scope
- Do NOT modify any files
- Do NOT deploy anything
- Do NOT install packages
- Do NOT create branches

## Success Criteria
1. I receive a complete report with all items above documented
2. I have enough detail to write a spec for a content agent that can commit properly formatted blog posts and open PRs

## Constraints
- Read-only. This is pure reconnaissance.
- SSH to Kush: `ssh 192.168.1.100` (Brad's user is `bradwood`)
- The repo path is `~/Projects/dbradwood.com`

## Execution
Use agent teams. Decompose this into sub-tasks and delegate to specialist agents. Do not execute sequentially as a single agent.
---
## Outcome
**Task ID:** 3ebe0b93-182b-4fb4-8d35-5390ab95d112
**QA pass rate:** 3/4 (incomplete: commands were loosely specified, output was partial)

## Retrospective
What worked: Recon completed and returned useful data about the dbradwood.com repo.

What caused friction: The spec was too loose. "Explore the repo" is not a spec. I respecced it as 39471a51 with 14 numbered commands and got a much better result. The first attempt wasted a task slot.

Spec improvement for next time: Even recon tasks need structure. Number the commands. Specify expected output format. Don't say "explore" when you mean "run these 14 specific commands and return verbatim output."
