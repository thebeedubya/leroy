#!/bin/bash
# daily-media-runner.sh
# Wrapper for daily media content pipeline
# Invoked by launchd at 6:00 AM CT daily

PROJECT_DIR="/Users/brad.wood/Projects/leroy"
COMMAND_FILE="${PROJECT_DIR}/.claude/commands/daily-media.md"
LOG_DATE=$(date +%Y-%m-%d)
LOG_FILE="${PROJECT_DIR}/content/logs/daily-media-${LOG_DATE}.log"
BRAIN_HEALTH_URL="http://192.168.1.100:8301/health"
CLAUDE_BIN="/Users/brad.wood/.local/bin/claude"

# Ensure log directory exists
mkdir -p "${PROJECT_DIR}/content/logs"

# All output goes to dated log file
exec >> "${LOG_FILE}" 2>&1

echo "=== daily-media-runner start: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "Log: ${LOG_FILE}"

# Check forge-brain health
echo "Checking forge-brain at ${BRAIN_HEALTH_URL}..."
if ! curl -sf --max-time 5 "${BRAIN_HEALTH_URL}" > /dev/null 2>&1; then
    echo "SKIP: forge-brain unreachable at ${BRAIN_HEALTH_URL} (timeout 5s)"
    echo "=== daily-media-runner exit: skipped (brain down) at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
    exit 0
fi
echo "OK: forge-brain healthy"

# Verify command file exists
if [ ! -f "${COMMAND_FILE}" ]; then
    echo "ERROR: command file not found: ${COMMAND_FILE}"
    echo "=== daily-media-runner exit: error (missing command file) ==="
    exit 1
fi

# Verify claude binary exists
if [ ! -x "${CLAUDE_BIN}" ]; then
    echo "ERROR: claude binary not found or not executable: ${CLAUDE_BIN}"
    echo "=== daily-media-runner exit: error (missing claude) ==="
    exit 1
fi

# Change to project directory so claude picks up .mcp.json
cd "${PROJECT_DIR}"

# Run the pipeline
echo "Running daily-media pipeline via claude -p..."
PROMPT=$(cat "${COMMAND_FILE}")
"${CLAUDE_BIN}" -p "${PROMPT}" --dangerously-skip-permissions
EXIT_CODE=$?

if [ ${EXIT_CODE} -eq 0 ]; then
    echo "SUCCESS: claude exited cleanly (code 0)"
else
    echo "WARNING: claude exited with code ${EXIT_CODE}"
fi

echo "=== daily-media-runner exit: code=${EXIT_CODE} at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
exit ${EXIT_CODE}
