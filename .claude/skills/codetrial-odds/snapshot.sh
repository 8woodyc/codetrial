#!/bin/sh
# Refresh the /codetrial-odds snapshot: run pr-sweep.sh against the last one,
# and replace it only with a whole sweep, so a failed or interrupted run keeps
# the old baseline. It lives in the git directory beside the triage review
# ledger, so it survives sessions and is never tracked, one per repository,
# since the sweep matches PRs by number alone.
#
# Usage: snapshot.sh [repo] (default sysprog21/codetrial)
#        Prints the snapshot's path; exits 1, printing nothing, when the sweep
#        failed. ODDS_SWEEP names the pr-sweep.sh to run, for tests.

set -eu

repo=${1:-sysprog21/codetrial}
sweep=${ODDS_SWEEP:-$(dirname "$0")/../codetrial-triage/pr-sweep.sh}
dir="$(git rev-parse --git-common-dir)/codetrial-triage"
mkdir -p "$dir"
last=$dir/pr-sweep-$(printf '%s' "$repo" | tr / -).json

# An empty stale-days leaves pr-sweep.sh its own default.
set -- "$repo" ""
if [ -s "$last" ]; then
    set -- "$@" "$last"
fi
tmp=$(mktemp "$dir/pr-sweep.XXXXXX")
trap 'rm -f "$tmp"' EXIT
if sh "$sweep" "$@" > "$tmp" && jq -e '.status | arrays' "$tmp" > /dev/null; then
    mv "$tmp" "$last"
    echo "$last"
else
    echo "snapshot.sh: the sweep failed; the last snapshot is kept" >&2
    exit 1
fi
