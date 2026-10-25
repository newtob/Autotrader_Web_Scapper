#!/usr/bin/env bash
# Run the scraper, retrying on failure until it succeeds or the retry window is used up.
#
# Usage:  scripts/run_with_retry.sh
#         RETRY_WINDOW=0 scripts/run_with_retry.sh        # single attempt, no retry
#
# Environment (all optional):
#   RETRY_INTERVAL  seconds between attempts, positive integer   (default 300)
#   RETRY_WINDOW    total seconds to keep retrying, integer >= 0  (default 3600)
#   RUN_CMD         command to run, executed with `bash -c`       (default "uv run python auto_trader.py")
#   LOG_DIR         directory for the per-run log                 (default <repo>/logs)
#
# Retries while elapsed + RETRY_INTERVAL <= RETRY_WINDOW (300 s / 3600 s gives 13 attempts).
# Every log line is timestamped and written to stdout and to LOG_DIR/<YYYY-MM-DD_HH-MM-SS>.log.
#
# Exit codes: 0 the command succeeded; otherwise the last failing exit code of the command
#             (any non-zero code retries); 64 invalid RETRY_INTERVAL, RETRY_WINDOW or RUN_CMD.
set -euo pipefail

readonly DEFAULT_RUN_CMD="uv run python auto_trader.py"
readonly EXIT_USAGE=64

log() {
    printf '%s [run_with_retry] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

die_usage() {
    log "ERROR: $*" >&2
    exit "$EXIT_USAGE"
}

require_int() {
    local name="$1" value="$2" min="$3"
    if [[ ! "$value" =~ ^[0-9]+$ ]] || ((value < min)); then
        die_usage "${name} must be an integer >= ${min}, got '${value}'"
    fi
}

run_with_retry() {
    local cmd="$1" interval="$2" window="$3"
    local attempt=0 rc=0
    SECONDS=0
    while true; do
        attempt=$((attempt + 1))
        log "attempt ${attempt}: ${cmd}"
        rc=0
        bash -c "$cmd" || rc=$?
        if ((rc == 0)); then
            log "attempt ${attempt} succeeded"
            return 0
        fi
        if ((SECONDS + interval > window)); then
            log "attempt ${attempt} failed with exit code ${rc}; retry window of ${window}s used up, giving up"
            return "$rc"
        fi
        log "attempt ${attempt} failed with exit code ${rc}; retrying in ${interval}s (${SECONDS}s of ${window}s used)"
        sleep "$interval"
    done
}

main() {
    local interval="${RETRY_INTERVAL:-300}"
    local window="${RETRY_WINDOW:-3600}"
    local cmd="${RUN_CMD:-$DEFAULT_RUN_CMD}"
    local repo_root log_dir log_file rc

    require_int RETRY_INTERVAL "$interval" 1
    require_int RETRY_WINDOW "$window" 0
    [[ -n "${cmd// /}" ]] || die_usage "RUN_CMD must not be empty"

    repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    log_dir="${LOG_DIR:-${repo_root}/logs}"
    mkdir -p "$log_dir"
    log_file="${log_dir}/$(date '+%Y-%m-%d_%H-%M-%S').log"
    cd "$repo_root"

    set +e
    run_with_retry "$cmd" "$interval" "$window" 2>&1 | tee -a "$log_file"
    rc="${PIPESTATUS[0]}"
    set -e
    return "$rc"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then main "$@"; fi
