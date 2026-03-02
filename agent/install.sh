#!/usr/bin/env bash
# Daily Media Agent — install / uninstall / status / run-now
#
# Usage:
#   ./install.sh            # install and activate the scheduled agent
#   ./install.sh uninstall  # remove the agent
#   ./install.sh status     # check whether the agent is loaded
#   ./install.sh run-now    # trigger an immediate run (manual override)

set -euo pipefail

PLIST_NAME="com.forge.daily-media"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_SRC="${SCRIPT_DIR}/${PLIST_NAME}.plist"
PLIST_DST="${HOME}/Library/LaunchAgents/${PLIST_NAME}.plist"
LOG_OUT="${HOME}/Library/Logs/leroy-daily-media.log"
LOG_ERR="${HOME}/Library/Logs/leroy-daily-media.err"

# Verify python3 is available
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3 first." >&2
    exit 1
fi

case "${1:-install}" in

    install)
        echo "Installing Daily Media Agent..."

        # Pre-flight: verify claude binary
        CLAUDE_BIN="${HOME}/.local/bin/claude"
        if [[ ! -x "${CLAUDE_BIN}" ]]; then
            echo "WARNING: claude not found at ${CLAUDE_BIN}."
            echo "         The agent will search fallback paths at runtime."
            echo "         Run 'which claude' to confirm the binary location,"
            echo "         then update CLAUDE_BIN in daily_media_agent.py if needed."
        fi

        # Ensure LaunchAgents directory exists
        mkdir -p "${HOME}/Library/LaunchAgents"

        # Copy plist
        cp "${PLIST_SRC}" "${PLIST_DST}"
        echo "  Plist installed: ${PLIST_DST}"

        # Create log files so they appear in Console.app immediately
        touch "${LOG_OUT}" "${LOG_ERR}"
        echo "  Logs: ${LOG_OUT}"

        # Ensure content directories exist
        mkdir -p "${SCRIPT_DIR}/../content/drafts"
        mkdir -p "${SCRIPT_DIR}/../content/logs"

        # Initialize agent-runs.json if absent
        RUNS_LOG="${SCRIPT_DIR}/../content/logs/agent-runs.json"
        if [[ ! -f "${RUNS_LOG}" ]]; then
            echo "[]" > "${RUNS_LOG}"
            echo "  Initialized: ${RUNS_LOG}"
        fi

        # Unload existing instance (ignore errors if not loaded)
        launchctl unload "${PLIST_DST}" 2>/dev/null || true

        # Load the agent
        launchctl load "${PLIST_DST}"
        echo "  Agent loaded."

        echo ""
        echo "Done. Daily Media Agent will run every day at 6:00 AM local time."
        echo ""
        echo "Commands:"
        echo "  Check status :  ${BASH_SOURCE[0]} status"
        echo "  Run now      :  ${BASH_SOURCE[0]} run-now"
        echo "  Uninstall    :  ${BASH_SOURCE[0]} uninstall"
        echo "  View logs    :  tail -f ${LOG_OUT}"
        ;;

    uninstall)
        echo "Uninstalling Daily Media Agent..."
        launchctl unload "${PLIST_DST}" 2>/dev/null || true
        rm -f "${PLIST_DST}"
        echo "Done. Logs remain at ${LOG_OUT}"
        ;;

    status)
        echo "launchd status for ${PLIST_NAME}:"
        launchctl list | grep "${PLIST_NAME}" || echo "  Not loaded."
        echo ""
        echo "Recent runs (last 5):"
        RUNS_LOG="${SCRIPT_DIR}/../content/logs/agent-runs.json"
        if [[ -f "${RUNS_LOG}" ]]; then
            python3 - "${RUNS_LOG}" <<'EOF'
import json, sys
with open(sys.argv[1]) as f:
    runs = json.load(f)
for r in runs[-5:]:
    print(f"  {r.get('timestamp','?')[:19]}  status={r.get('status','?')}  date={r.get('target_date','?')}  angles={r.get('draft_count','?')}")
EOF
        else
            echo "  No run log found."
        fi
        ;;

    run-now)
        echo "Triggering immediate run of Daily Media Agent..."
        launchctl start "${PLIST_NAME}"
        echo "Started. Check logs: tail -f ${LOG_OUT}"
        ;;

    *)
        echo "Usage: $0 {install|uninstall|status|run-now}"
        exit 1
        ;;
esac
