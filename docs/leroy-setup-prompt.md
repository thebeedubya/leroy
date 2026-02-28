# Leroy Repo Setup -- Code Prompt

## Objective

Wire up the leroy repo on Haze so a CLI Claude session started with `cd ~/leroy && claude` loads the PM persona with correct MCP servers and tool restrictions. PM must be able to query forge-brain and A2A but must NOT be able to execute bash, edit files, or write code.

## Context

The leroy directory already exists at `~/leroy/` with these files:
- CLAUDE.md (PM persona and constraints)
- README.md
- .claude/settings.json (permissions config)
- personas/pm.md, engineering_lead.md, shared_context.md
- sdlc/micro_sprint.md, escalation.md, qa_requirements.md
- Empty directories: server/, mcp/, app/, docs/

These files were written by PM (Desktop Claude) and placed by Brad. Do NOT modify the content of CLAUDE.md, personas/, or sdlc/ files. Your job is infrastructure wiring, not content changes.

## Scope

### IN scope:
1. Register forge-brain MCP server for the leroy project
2. Register a2a MCP server for the leroy project
3. Configure tool permissions to deny bash, file edit, and write tools for PM sessions
4. Verify the configuration works end-to-end
5. Initialize git repo if not already initialized
6. Create .gitignore

### OUT of scope:
- Building the A2A server (future sprint)
- Building MCP tool wrappers (future sprint)
- Building the proxy guard (only needed if permissions deny doesn't work)
- Modifying any persona or SDLC files
- macOS app (way future)

## Implementation Steps

### 1. Git Init
```bash
cd ~/leroy
git init  # if not already a repo
```

### 2. Create .gitignore
```
.DS_Store
__pycache__/
*.pyc
.env
*.egg-info/
dist/
build/
node_modules/
.venv/
venv/
```

### 3. Register MCP Servers (Project-Scoped)

IMPORTANT: Claude Code reads MCP config from `~/.claude.json`, NOT from `.claude/settings.json`. The `.claude/settings.json` file in the repo is for permissions only. MCP servers must be registered via CLI commands.

```bash
cd ~/leroy

claude mcp add-json forge-brain '{
  "type": "sse",
  "url": "http://192.168.1.100:8300/sse",
  "env": {
    "FORGE_BRAIN_SOURCE": "leroy-pm",
    "FORGE_BRAIN_MACHINE": "haze"
  }
}' -s project

claude mcp add-json a2a '{
  "type": "sse",
  "url": "https://155.138.199.82:8443/a2a"
}' -s project
```

Verify registration:
```bash
claude mcp list -s project
```

Both `forge-brain` and `a2a` should appear.

### 4. Configure Permissions

The `.claude/settings.json` file already has a permissions block. Verify it contains:

```json
{
  "permissions": {
    "allow": [
      "mcp__forge-brain__*",
      "mcp__a2a__*",
      "WebSearch"
    ],
    "deny": [
      "Bash(*)",
      "Edit(*)",
      "Write(*)",
      "MultiEdit(*)"
    ]
  }
}
```

If Claude Code uses a different format for the permissions deny list, adapt accordingly. The intent is: PM sessions in this project directory cannot use bash, cannot edit files, cannot write files. Only MCP tools (brain, a2a) and web search are allowed.

Research how Claude Code project-level permissions actually work. The key question: does `.claude/settings.json` in the project root get respected by `claude` CLI for tool permissions? If not, find the correct mechanism. Options to investigate:
- `.claude/settings.json` permissions block
- `.claude/settings.local.json`
- `claude config set` commands
- `--allowedTools` or `--disallowedTools` CLI flags
- CLAUDE.md instructions (soft constraint, not hard)

We need a HARD constraint, not just CLAUDE.md instructions. The PM must be physically unable to run bash, not just told not to.

### 5. Validation

Start a claude session in the leroy directory and test:

```bash
cd ~/leroy
claude --print "What MCP tools do you have access to? List them."
```

Expected: forge-brain tools and a2a tools appear. Bash, Edit, Write, MultiEdit do NOT appear.

```bash
claude --print "Run the command: echo hello"
```

Expected: Refused or blocked. Should NOT execute bash.

```bash
claude --print "Query forge-brain memory_status"
```

Expected: Returns brain health stats successfully.

If `--print` doesn't support tool execution testing, start an interactive session and test manually. Document what you find.

### 6. Git Commit

```bash
cd ~/leroy
git add -A
git commit -m "Initial scaffold: PM persona, SDLC docs, MCP config, permissions"
```

If Brad has a GitHub remote for leroy, push. If not, local commit is fine.

## Success Criteria

1. `cd ~/leroy && claude mcp list -s project` shows forge-brain and a2a registered
2. A claude session started in ~/leroy/ loads CLAUDE.md automatically (PM persona)
3. forge-brain MCP tools are accessible from the session (query_memory, persist_on, etc.)
4. a2a MCP tools are accessible from the session
5. Bash execution is BLOCKED (hard deny, not just CLAUDE.md instruction)
6. File editing is BLOCKED (hard deny)
7. File writing is BLOCKED (hard deny)
8. Git repo initialized with clean .gitignore and initial commit
9. No modifications to any content files (CLAUDE.md, personas/, sdlc/)

## Constraints

- Do NOT modify CLAUDE.md, personas/*.md, or sdlc/*.md content
- Do NOT install new packages or dependencies (none needed for this step)
- Do NOT create server code (that's a future sprint)
- If permissions deny doesn't work as a hard constraint, document exactly what you tried and what happened. That becomes the proxy guard build ticket.
- forge-brain is running at http://192.168.1.100:8300/sse (HTTP+SSE transport, already verified)
- a2a gateway is at https://155.138.199.82:8443/a2a (mTLS, certs already on Haze)

## Debug Until Working

If MCP registration fails, check:
- Is forge-brain HTTP service running on Kush? `curl http://192.168.1.100:8300/sse` should respond
- Are MCP servers registering to the right scope? `claude mcp list -s project` vs `claude mcp list -s user`
- Is the project directory recognized? Check `~/.claude.json` for a leroy project entry

If permissions deny doesn't work, try:
- Different permission string formats (e.g., "Bash" vs "Bash(*)" vs "bash")
- Project-level vs user-level settings
- CLI flags for tool restriction
- Document everything you try. Do not give up after one attempt.

The goal is a working PM CLI session with hard tool restrictions. This is the foundation for the entire Leroy agent orchestration system. Get it right.
