---
spec_id: dbradwoodcom-repo-recon-read-only-ssh-reconnaissan
task_id: 39471a51-2e5f-4091-9668-f41d53e6003e
date: 2026-03-01
status: completed
pass_rate: 4/4
retrospective: What worked: Recon spec was thorough. 14 commands, all executed verbatim, zero writes. The output gave us a complete picture of the dbradwood.com stack (Next.js 16, React 19, Tailwind v4, Zod validation, MDX pipeline). Leroy executed via SSH to Kush cleanly.  What caused friction: This was a duplicate of 3ebe0b93 (first recon attempt). The first attempt was less structured. I should have written the structured version first instead of iterating.  Spec improvement for next time: For recon specs, always provide numbered commands with exact expected output format. The structured approach (command 1, command 2, etc.) worked much better than the first attempt's looser "explore and report" framing.
tags: []
---

# Spec: dbradwood.com Repository Reconnaissance

## Objective

Perform read-only reconnaissance on the dbradwood.com Next.js repository hosted on Kush. Capture all command output verbatim and return it as a structured report. This is pure information gathering, no changes of any kind.

## Scope

### In Scope
- SSH into Kush (192.168.1.100) as bradwood
- Run the specified commands exactly as listed
- Capture ALL output verbatim, no truncation, no summarization
- Return a single structured report with each command's output labeled

### Out of Scope
- Modifying any files
- Installing packages
- Deploying anything
- Creating branches
- Any write operations whatsoever

## Commands to Run (in order)

Run each command exactly as written. Capture stdout and stderr verbatim.

1. `ssh bradwood@192.168.1.100 "ls -la ~/Projects/dbradwood.com"`

2. `ssh bradwood@192.168.1.100 "ls -la ~/Projects/dbradwood.com/src 2>/dev/null || ls -la ~/Projects/dbradwood.com/app 2>/dev/null || echo 'no src or app dir at top level'"`

3. `ssh bradwood@192.168.1.100 "find ~/Projects/dbradwood.com -name '*.md' -o -name '*.mdx' | grep -v node_modules | grep -v .next | head -30"`

4. `ssh bradwood@192.168.1.100 "find ~/Projects/dbradwood.com -type d -name 'content' -o -type d -name 'posts' -o -type d -name 'blog' -o -type d -name 'writing' 2>/dev/null | grep -v node_modules | grep -v .next"`

5. `ssh bradwood@192.168.1.100 "cat ~/Projects/dbradwood.com/package.json"`

6. `ssh bradwood@192.168.1.100 "cat ~/Projects/dbradwood.com/next.config.js 2>/dev/null || cat ~/Projects/dbradwood.com/next.config.mjs 2>/dev/null || cat ~/Projects/dbradwood.com/next.config.ts 2>/dev/null || echo 'no next.config found'"`

7. `ssh bradwood@192.168.1.100 "cat ~/Projects/dbradwood.com/vercel.json 2>/dev/null || echo 'no vercel.json'"`

8. `ssh bradwood@192.168.1.100 "ls -la ~/Projects/dbradwood.com/.vercel 2>/dev/null || echo 'no .vercel dir'"`

9. `ssh bradwood@192.168.1.100 "which vercel 2>/dev/null && vercel --version 2>/dev/null || echo 'vercel CLI not found'"`

10. `ssh bradwood@192.168.1.100 "cd ~/Projects/dbradwood.com && git remote -v"`

11. `ssh bradwood@192.168.1.100 "cd ~/Projects/dbradwood.com && git branch -a"`

12. `ssh bradwood@192.168.1.100 "cd ~/Projects/dbradwood.com && git log --oneline -5"`

13. Find markdown/MDX files then cat them:
    - First: `ssh bradwood@192.168.1.100 "find ~/Projects/dbradwood.com -name '*.md' -o -name '*.mdx' | grep -v node_modules | grep -v .next | head -5"`
    - Then cat each file returned, full contents, no truncation.

14. `ssh bradwood@192.168.1.100 "ls -la ~/Projects/dbradwood.com/.vercel/ 2>/dev/null && cat ~/Projects/dbradwood.com/.vercel/project.json 2>/dev/null || echo 'no .vercel/project.json'"`

## Output Format

Return a single artifact structured as follows:

```
## dbradwood.com Recon Report
**Date:** {date}
**Executed by:** Leroy
**Machine:** Kush (192.168.1.100)

---

### Command 1: Root directory listing
```
{verbatim output}
```

### Command 2: src/app directory
```
{verbatim output}
```

[...continue for all 14 commands...]
```

Do not summarize. Do not interpret. Do not truncate. Just capture and return raw output.

## Success Criteria

- All 14 commands executed
- All output returned verbatim, no truncation
- Report is structured with each command labeled
- Zero write operations performed on the target machine

## Constraints

- Read-only. No exceptions.
- Do not install anything on Kush.
- Do not modify the repository.
- Do not create branches.
- Do not run npm install, yarn, or any package manager commands.
- Do not deploy.
- SSH key auth should work. If it fails, report the error verbatim.

## Machine Context

- Target: Kush at 192.168.1.100
- SSH user: bradwood
- Repo path: ~/Projects/dbradwood.com
- Execution machine: Haze (local dev machine)

## Execution

Use agent teams. Decompose this into sub-tasks and delegate to specialist agents. Do not execute sequentially as a single agent.

---
## Outcome
**Task ID:** 39471a51-2e5f-4091-9668-f41d53e6003e
**QA pass rate:** 4/4

## Retrospective
What worked: Recon spec was thorough. 14 commands, all executed verbatim, zero writes. The output gave us a complete picture of the dbradwood.com stack (Next.js 16, React 19, Tailwind v4, Zod validation, MDX pipeline). Leroy executed via SSH to Kush cleanly.

What caused friction: This was a duplicate of 3ebe0b93 (first recon attempt). The first attempt was less structured. I should have written the structured version first instead of iterating.

Spec improvement for next time: For recon specs, always provide numbered commands with exact expected output format. The structured approach (command 1, command 2, etc.) worked much better than the first attempt's looser "explore and report" framing.
