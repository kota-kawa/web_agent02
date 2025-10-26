#!/bin/bash
set -euo pipefail

WINDOW_NAME="Chrome"
MAX_ATTEMPTS=30
SLEEP_INTERVAL=1

if ! command -v wmctrl >/dev/null 2>&1; then
    echo "wmctrl is required but not installed." >&2
    exit 1
fi

if ! command -v xprop >/dev/null 2>&1; then
    echo "xprop is required but not installed." >&2
    exit 1
fi

/opt/bin/entry_point.sh "$@" &
ENTRYPOINT_PID=$!

cleanup() {
    if kill -0 "$ENTRYPOINT_PID" >/dev/null 2>&1; then
        kill "$ENTRYPOINT_PID" >/dev/null 2>&1 || true
    fi
}

trap 'cleanup; wait "$ENTRYPOINT_PID"; exit' INT TERM

echo "Waiting for ${WINDOW_NAME} window to become available..."

get_window_id() {
    wmctrl -lx 2>/dev/null | awk -v name="$WINDOW_NAME" 'tolower($0) ~ tolower(name) {print $1; exit}'
}

wait_for_window() {
    local attempt window_id
    for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
        window_id=$(get_window_id)
        if [[ -n "$window_id" ]]; then
            return 0
        fi
        sleep "$SLEEP_INTERVAL"
    done
    return 1
}

verify_maximized() {
    local window_id
    window_id=$(get_window_id)
    if [[ -z "$window_id" ]]; then
        return 1
    fi

    local state
    state=$(xprop -id "$window_id" _NET_WM_STATE 2>/dev/null || true)

    [[ "$state" == *"_NET_WM_STATE_MAXIMIZED_VERT"* ]] && \
        [[ "$state" == *"_NET_WM_STATE_MAXIMIZED_HORZ"* ]]
}

maximize_window() {
    wmctrl -r "$WINDOW_NAME" -b add,maximized_vert,maximized_horz
}

if wait_for_window; then
    success=false
    for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
        if maximize_window && verify_maximized; then
            echo "${WINDOW_NAME} window maximized successfully."
            success=true
            break
        fi
        if (( attempt < MAX_ATTEMPTS )); then
            sleep "$SLEEP_INTERVAL"
            echo "Retrying to maximize ${WINDOW_NAME} window (attempt ${attempt}/${MAX_ATTEMPTS})."
        fi
    done
    if [[ "$success" == false ]]; then
        echo "Failed to verify maximization of the ${WINDOW_NAME} window after ${MAX_ATTEMPTS} attempts." >&2
    fi
else
    echo "Chrome window was not detected; skipping forced maximization." >&2
fi

wait "$ENTRYPOINT_PID"
