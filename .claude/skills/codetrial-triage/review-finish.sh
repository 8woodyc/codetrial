#!/bin/sh
# Finish one PR's sweep review: post its inline comments if the head is still
# the one the agent read, and record the head in the ledger only when nothing
# else is left to post (review-prs.md, "Verify and post each PR as it lands").
# The caller has already verified each finding at the head; this is the part
# that was typed by hand per PR.
#
# Usage: review-finish.sh <pr> <draft.json> [repo]
#        Exits 0 once the head is in the ledger. Exits 4, with the pending
#        work printed, when a draft thread is left to resolve (that needs the
#        checks in review-prs.md, so the head is recorded by hand once it is
#        done) or a similar-work notice could not be posted. Exits 3 when the
#        head moved and 1 when post-inline.sh failed; in both the ledger is
#        untouched, though a fallback post may already have landed some
#        comments, which a rerun does not repeat. Exits 2 on a bad argument or
#        a draft that is not {pr, sha, comments, similar, resolve} for this PR.

set -eu

usage='usage: review-finish.sh <pr> <draft.json> [repo]'
[ $# -ge 2 ] || {
    echo "$usage" >&2
    exit 2
}
pr=$1
draft=$2
repo=${3:-sysprog21/codetrial}
case $pr in
    *[!0-9]*)
        echo "review-finish.sh: $pr is not a pull request number" >&2
        exit 2
        ;;
esac

# Every field the script branches on is checked here, so a malformed draft fails
# before any call and can never reach the ledger append below.
jq -se --argjson pr "$pr" 'length == 1 and (.[0]
    | type == "object" and (.sha | type == "string" and test("^[^[:space:]]+$"))
      and .pr == $pr
      and ([.comments, .similar, .resolve] | all(type == "array")))' \
    "$draft" > /dev/null 2>&1 || {
    echo "review-finish.sh: $draft is not a draft for #$pr" >&2
    exit 2
}
here=$(cd "$(dirname "$0")" && pwd)
inline=${REVIEW_FINISH_INLINE:-$here/../codetrial-review/post-inline.sh}

# One read of the draft for what the steps below branch on, taken as a checked
# assignment first: a failure inside the here-document would not stop the
# script.
fields=$(jq -r '[.sha, (.comments | length), (.similar | length), (.resolve | length)] | @tsv' "$draft")
read -r sha comments similar resolve << DRAFT
$fields
DRAFT

# A draft whose notices cannot be posted is refused now, not after the inline
# review has already landed.
if [ "$similar" -gt 0 ]; then
    "$here/post-similar.sh" --check "$pr" "$draft" "$repo" || exit 2
fi
live=$(gh pr view "$pr" --repo "$repo" --json headRefOid --jq .headRefOid)
if [ "$live" != "$sha" ]; then
    echo "review-finish.sh: #$pr moved to $live; $sha was reviewed" >&2
    exit 3
fi

if [ "$comments" -gt 0 ]; then
    "$inline" "$pr" "$draft" "$repo"
fi

# Similar-work notices go out through post-similar.sh, which needs SCRATCH. A
# notice that could not be posted (a head moved, a comment refused) leaves the
# head out of the ledger, like a thread still to resolve.
pending=false
if [ "$similar" -gt 0 ]; then
    "$here/post-similar.sh" "$pr" "$draft" "$repo" || pending=true
fi
if [ "$resolve" -gt 0 ]; then
    jq '{resolve}' "$draft"
    pending=true
fi
if $pending; then
    exit 4
fi

ledger="$(git rev-parse --git-common-dir)/codetrial-triage/reviewed"
mkdir -p "$(dirname "$ledger")"
echo "$pr $sha" >> "$ledger"
