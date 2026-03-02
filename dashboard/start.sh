#!/usr/bin/env bash
# Leroy Dashboard -- dev server launcher
# Handles macOS Homebrew node not being in shell PATH
set -e

DASHBOARD_DIR="$(cd "$(dirname "$0")" && pwd)"

# Find node
NODE_BIN=""
for candidate in "/opt/homebrew/bin/node" "/usr/local/bin/node" "$(which node 2>/dev/null)"; do
    if [ -x "$candidate" ]; then
        NODE_BIN="$candidate"
        break
    fi
done

if [ -z "$NODE_BIN" ]; then
    echo "Error: node not found. Install via: brew install node"
    exit 1
fi

NODE_DIR="$(dirname "$NODE_BIN")"
export PATH="$NODE_DIR:$PATH"

echo "Leroy Dashboard starting..."
echo "URL: http://localhost:5173"
echo "API: http://127.0.0.1:9800 (Leroy A2A server must be running)"
echo ""

cd "$DASHBOARD_DIR"
"$NODE_BIN" "$NODE_DIR/npm" run dev
