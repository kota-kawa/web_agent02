#!/usr/bin/env bash
set -euo pipefail

WINDOW_TITLE=${1:-${CHROME_WINDOW_TITLE:-Chrom}}
MAX_RETRIES=${2:-${CHROME_MAXIMIZE_RETRIES:-60}}
SLEEP_SECONDS=${3:-${CHROME_MAXIMIZE_RETRY_INTERVAL:-1}}
WAIT_FOR_FLUXBOX=${CHROME_WAIT_FOR_FLUXBOX:-1}

log() {
    printf '[maximize-chrome] %s\n' "$1"
}

if [[ -z "${DISPLAY:-}" ]]; then
    log "DISPLAY is not set; skipping maximize attempts"
    exit 0
fi

if ! command -v wmctrl >/dev/null 2>&1; then
    log "wmctrl is not installed; skipping maximize attempts"
    exit 0
fi

if [[ "$WAIT_FOR_FLUXBOX" == "1" ]]; then
    fluxbox_retries=${CHROME_WAIT_FOR_FLUXBOX_RETRIES:-30}
    fluxbox_interval=${CHROME_WAIT_FOR_FLUXBOX_INTERVAL:-1}
    for ((i = 1; i <= fluxbox_retries; i++)); do
        if pgrep -x fluxbox >/dev/null 2>&1; then
            break
        fi
        log "Waiting for fluxbox to start... (${i}/${fluxbox_retries})"
        sleep "$fluxbox_interval"
    done
    if ! pgrep -x fluxbox >/dev/null 2>&1; then
        log "Fluxbox did not start within ${fluxbox_retries} attempts; continuing"
    fi
fi

for ((i = 1; i <= MAX_RETRIES; i++)); do
    if wmctrl -l | grep -i "$WINDOW_TITLE" >/dev/null 2>&1; then
        if wmctrl -r "$WINDOW_TITLE" -b add,maximized_vert,maximized_horz; then
            log "Maximized window '$WINDOW_TITLE'"
            exit 0
        fi
    fi
    log "Waiting for Chrome window '$WINDOW_TITLE'... (${i}/${MAX_RETRIES})"
    sleep "$SLEEP_SECONDS"
fi

log "Failed to maximize window '$WINDOW_TITLE' after ${MAX_RETRIES} attempts"
exit 1
