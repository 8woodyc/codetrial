# Sourced, so no shebang: the dialect is stated for shellcheck instead.
# shellcheck shell=sh

# Sourced by the check scripts that start a server of their own.
#
# Usage: wait_for_server URL SECONDS LOG [PID [OUTPUT]] || exit 1
#
# Returns 0 once URL answers, with the response in OUTPUT (default: discarded).
# Returns 1 after at least SECONDS of wall time without an answer (the clock is
# read in whole seconds, so it may be up to a second more), or at once when PID
# had already exited before the last probe, since a server that is gone will
# never answer and waiting out the limit only delays the log that says why.
# Liveness is read before the probe, not after, so a process that answers and
# then exits is a success rather than a race. A probe is cut off after five
# seconds or what is left of the limit, whichever is shorter: a server that
# accepts the connection and never replies would otherwise hold the first curl
# past any limit. LOG is printed on failure: a server still starting under
# `cargo run --quiet` may have written nothing yet, and a bare exit 1 is all CI
# showed when one ran out.

wait_for_server()
{
    wait_url=$1
    wait_limit=$2
    wait_log=$3
    wait_pid=${4:-}
    wait_out=${5:-/dev/null}
    wait_deadline=$(($(date +%s) + wait_limit + 1))

    while :; do
        wait_alive=1
        if [ -n "$wait_pid" ] && ! kill -0 "$wait_pid" 2> /dev/null; then
            wait_alive=0
        fi
        wait_cap=$((wait_deadline - $(date +%s)))
        if [ "$wait_cap" -gt 5 ]; then
            wait_cap=5
        elif [ "$wait_cap" -lt 1 ]; then
            wait_cap=1
        fi
        if curl -fsS -m "$wait_cap" "$wait_url" > "$wait_out" 2> /dev/null; then
            return 0
        fi
        if [ "$wait_alive" -eq 0 ]; then
            echo "The server exited before $wait_url answered." >&2
        elif [ "$(date +%s)" -ge "$wait_deadline" ]; then
            echo "$wait_url did not answer within $wait_limit seconds." >&2
        else
            sleep 1
            continue
        fi
        if [ -s "$wait_log" ]; then
            cat "$wait_log" >&2
        fi
        return 1
    done
}
