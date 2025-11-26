#!/bin/bash
set -euo pipefail

/opt/bin/entry_point.sh "$@" &
ENTRYPOINT_PID=$!

# Allow Chrome some time to start before attempting to maximize the window.
sleep 5

# Try multiple times in case the window is not immediately available.
for _ in {1..10}; do
    if wmctrl -r "Chrome" -b add,maximized_vert,maximized_horz 2>/dev/null; then
        break
    fi
    sleep 1
done

wait "$ENTRYPOINT_PID"
