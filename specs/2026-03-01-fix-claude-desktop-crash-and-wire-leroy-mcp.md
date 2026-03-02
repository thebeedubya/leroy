---
spec_id: fix-claude-desktop-crash-and-wire-leroy-mcp
task_id: 71ae577b-089d-4b98-ad3e-df717dd509e4
date: 2026-03-01
status: completed
pass_rate: incomplete (no subtask results reported, cannot verify success criteria)
retrospective: What worked: Spec was thorough with investigation steps, explicit config context, and clear success criteria. Gave Leroy everything needed to diagnose.  What caused friction: Result is empty -- "No subtasks reported." Cannot tell what the crash root cause was, whether the config was fixed, or whether the leroy MCP server was added. This is the worst possible outcome for a retro: I have no data. The stall bug likely ate the result before it was written. Ops marked it complete to unstick it but the actual work product is unknown.  Spec improvement for next time: This task needs to be re-verified. Did Desktop actually get fixed? Is leroy MCP wired? I have zero evidence either way. The stall bug fix (54b7de7d) should prevent this from happening again, but I need to send a follow-up verification task or ask Brad directly whether Desktop is launching.
tags: []
---

# Fix Claude Desktop Crash on Launch + Wire Leroy MCP

## Objective

Claude Desktop fails to launch with "Claude Desktop failed to Launch" error. Fix whatever is crashing it and add the Leroy MCP server so Desktop can interact with the Leroy A2A task system.

## Current State

Desktop config at `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "youtube-transcript": {
      "command": "/Users/brad.wood/youtube-mcp-server/.venv/bin/python",
      "args": ["/Users/brad.wood/youtube-mcp-server/server.py"]
    },
    "aianna": {
      "type": "http",
      "url": "http://192.168.1.100:8300/mcp"
    },
    "a2a": {
      "command": "node",
      "args": ["/Users/brad.wood/Projects/forge-ecosystem/a2a-mcp-server/index.js"],
      "env": {
        "A2A_GATEWAY_URL": "https://155.138.199.82:8443/a2a",
        "A2A_CERT_PATH": "/Users/brad.wood/forge-cert.pem",
        "A2A_KEY_PATH": "/Users/brad.wood/forge-key.pem",
        "A2A_CA_PATH": "/Users/brad.wood/ca-cert.pem",
        "A2A_API_KEY": "CLOUDRAIDER_API_KEY_REDACTED"
      }
    }
  },
  "preferences": {
    "chromeExtension": {
      "pairedDeviceId": "ab5b97d6-9459-4278-9681-d014ecea6aa5",
      "pairedDeviceName": "work laptop"
    },
    "coworkScheduledTasksEnabled": true,
    "sidebarMode": "chat",
    "bypassPermissionsModeEnabled": true,
    "coworkWebSearchEnabled": true
  }
}
```

## Known Facts

- forge-brain (aianna) is running on Kush 192.168.1.100:8300. Auth is DISABLED (token_auth:false). No auth headers needed.
- A2A gateway is fixed and operational.
- mcp-remote proxy is dead. Native HTTP transport is the current standard.
- The forge-ecosystem agent recently updated transport configs. The Desktop config may not match current working patterns.

## Investigation Steps

1. **Check Desktop crash logs.** Look in `~/Library/Logs/Claude/`, Console.app, or any crash report in `~/Library/Logs/DiagnosticReports/`. Find the actual error, not the generic "failed to launch" message.

2. **Test each MCP server independently:**
   - youtube-transcript: Run `/Users/brad.wood/youtube-mcp-server/.venv/bin/python /Users/brad.wood/youtube-mcp-server/server.py` and see if it starts
   - aianna: `curl -s http://192.168.1.100:8300/mcp` to verify the endpoint responds. Also check if `"type": "http"` is a valid Desktop config format (check Claude Desktop docs or other working configs)
   - a2a: Run `node /Users/brad.wood/Projects/forge-ecosystem/a2a-mcp-server/index.js` with the env vars and see if it starts

3. **Find the culprit.** One of these three is crashing on init and taking Desktop down with it. Identify which one.

4. **Fix the config.** Whatever is broken, fix it in `claude_desktop_config.json`. Do not change working servers. Only fix what's broken.

5. **Add Leroy MCP server.** Add this entry to the mcpServers block:
   - Name: `leroy`
   - Type: STDIO
   - Command: Python from the leroy project's venv
   - Script: `/Users/brad.wood/Projects/leroy/mcp/leroy_client.py`
   - The venv is at `/Users/brad.wood/Projects/leroy/mcp/` -- find the correct Python path (check for .venv or venv directory, or check how pm.sh or .mcp.json references it)

6. **Launch Desktop and verify.** Open Claude Desktop, confirm it starts without crashing, confirm all 4 MCP servers connect.

## Success Criteria

1. Root cause of crash identified (specific MCP server and error message)
2. Claude Desktop launches successfully with no crash
3. aianna MCP tools functional in Desktop (test: can you call query_memory?)
4. a2a MCP tools functional in Desktop
5. youtube-transcript MCP tools functional in Desktop
6. leroy MCP tools functional in Desktop (test: can you call leroy_list_tasks?)
7. Config file is valid JSON with all 4 MCP servers

## Constraints

- Only modify `~/Library/Application Support/Claude/claude_desktop_config.json`
- Do not modify any MCP server code
- Do not modify forge-brain, A2A gateway, or Leroy server
- Auth is disabled on forge-brain. Do not add auth headers.
- Do not install new packages or dependencies

## Do Not Do

- Do not re-enable auth on forge-brain
- Do not add mcp-remote (it's dead)
- Do not modify any .mcp.json files (those are for Claude Code, not Desktop)
- Do not restart forge-brain or the Leroy A2A server

## Machine Details

- Haze (local): Desktop config, all MCP client processes
- Kush (192.168.1.100): forge-brain at port 8300, health at 8301
- APEX (155.138.199.82): A2A gateway at port 8443

## Budget

Simple. Troubleshooting + config edit. Under 15 minutes.

## Execution

Use agent teams. Decompose this into sub-tasks and delegate to specialist agents. Do not execute sequentially as a single agent.

---
## Outcome
**Task ID:** 71ae577b-089d-4b98-ad3e-df717dd509e4
**QA pass rate:** incomplete (no subtask results reported, cannot verify success criteria)

## Retrospective
What worked: Spec was thorough with investigation steps, explicit config context, and clear success criteria. Gave Leroy everything needed to diagnose.

What caused friction: Result is empty -- "No subtasks reported." Cannot tell what the crash root cause was, whether the config was fixed, or whether the leroy MCP server was added. This is the worst possible outcome for a retro: I have no data. The stall bug likely ate the result before it was written. Ops marked it complete to unstick it but the actual work product is unknown.

Spec improvement for next time: This task needs to be re-verified. Did Desktop actually get fixed? Is leroy MCP wired? I have zero evidence either way. The stall bug fix (54b7de7d) should prevent this from happening again, but I need to send a follow-up verification task or ask Brad directly whether Desktop is launching.
