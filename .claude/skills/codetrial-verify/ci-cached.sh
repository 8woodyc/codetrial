#!/bin/sh
# ci-failure.sh behind a short head-pinned cache, so one sweep reads each PR's
# job logs once: pr-sweep.sh asks about stale bases and hygiene.sh asks about
# the CI item, both of the same head. Same arguments, same output.
#
# Usage: ci-cached.sh <pr> [repo]
#        CI_FAILURE_CACHE names the cache directory (default: beside the triage
#        review ledger in the git directory, never tracked); CI_FAILURE_TTL is
#        its age limit in minutes (default 5); CI_FAILURE_SCRIPT names the
#        ci-failure.sh to ask, for tests.
#
# An entry is keyed by repository, PR, head and a checksum of the PR's check
# list (names, states and links), and stored only when nothing is pending or
# held, and only for a PR with a failed check, the one case with job logs worth
# keeping. A push is a new head, and a rerun of a job on the same head changes
# its state and link, so neither is served an old diagnosis; a check list that
# cannot be read bypasses the cache. What the checks cannot show is main's
# latest run, which stale_base and on_main read: that part is as old as the TTL,
# kept short because a sweep acts on it without asking.

set -eu

pr=${1:?usage: ci-cached.sh <pr> [repo]}
repo=${2:-sysprog21/codetrial}
case $pr in
    *[!0-9]*)
        echo "ci-cached.sh: $pr is not a pull request number" >&2
        exit 2
        ;;
esac
script=${CI_FAILURE_SCRIPT:-$(dirname "$0")/ci-failure.sh}
dir=${CI_FAILURE_CACHE:-$(git rev-parse --git-common-dir)/codetrial-triage/ci}
mkdir -p "$dir"

# gh pr checks exits non-zero when a check fails and still prints them all, so
# only an array on stdout says the list was read. The list comes first: a PR
# with no failed check has no job log to read, so it skips the cache and the
# head lookup and costs one call more than ci-failure.sh alone.
checks=$(gh pr checks "$pr" --repo "$repo" --json name,state,bucket,link || true)
if ! printf '%s\n' "$checks" | jq -se 'map(type) == ["array"]
    and (.[0] | any(.bucket == "fail" or .bucket == "cancel"))' > /dev/null 2>&1; then
    exec sh "$script" "$pr" "$repo"
fi
head=$(gh pr view "$pr" --repo "$repo" --json headRefOid --jq .headRefOid)
# The head becomes part of a file name only once it is known to be a commit id.
case $head in
    *[!0-9a-f]* | "")
        echo "ci-cached.sh: #$pr has no readable head" >&2
        exit 1
        ;;
esac

# The repository enters the name as a checksum, so a/b-c and a-b/c, which a
# character swap would join, keep separate entries.
sig=$(printf '%s' "$checks" | cksum | tr ' ' -)
entry=$dir/$(printf '%s' "$repo" | cksum | tr ' ' -)-$pr-$head-$sig.json

if [ -n "$(find "$entry" -mmin "-${CI_FAILURE_TTL:-5}" 2> /dev/null)" ]; then
    cat "$entry"
    exit 0
fi
tmp=$(mktemp "$dir/ci.XXXXXX")
trap 'rm -f "$tmp"' EXIT
sh "$script" "$pr" "$repo" > "$tmp"
if jq -e --arg head "$head" '.head == $head and (.pending | length == 0)
    and (.held | length == 0)' "$tmp" > /dev/null 2>&1; then
    mv "$tmp" "$entry"
    cat "$entry"
else
    cat "$tmp"
fi
