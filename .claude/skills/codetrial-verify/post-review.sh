#!/bin/sh
# Post a review the user confirmed, only while the pull request is still where
# it was when they read it. The payload is the one review.md describes, already
# passed through review-anchors.sh; updatedAt is the value recorded when the
# text was shown to the user.
#
# Usage: post-review.sh <pr> <checked.json> <updatedAt> [repo]
#        Prints the posted review's URL. Exits 3, posting nothing, when the
#        head moved, the PR was updated since, or a comment would land outside
#        the diff; 2 on bad arguments.
#
# A new head means a new diff, so the anchors and the findings both need reading
# again. A new comment or edit at the same head may answer a finding, which is
# why updatedAt is compared too. The anchors are checked once more rather than
# trusted: GitHub refuses the whole review over one bad line, and
# review-anchors.sh would move it into the body, which is text the user did not
# confirm.

set -eu

usage='usage: post-review.sh <pr> <checked.json> <updatedAt> [repo]'
pr=${1:?$usage}
payload=${2:?$usage}
since=${3:?$usage}
repo=${4:-sysprog21/codetrial}

# review-anchors.sh refuses a pull request that is not a number or a payload
# that is not one JSON object, with exit 2 and its own message, before any call
# to GitHub; set -e passes that exit on. Its count of inline comments goes to
# stderr too. The checks below read its output, the text that is posted.
checked=$(sh "$(dirname "$0")/review-anchors.sh" "$pr" "$payload" "$repo")
want=$(printf '%s\n' "$checked" | jq -er '.commit_id | strings') || {
    echo "post-review.sh: $payload names no commit_id" >&2
    exit 2
}

# Without an event GitHub keeps the review pending, visible to nobody else, and
# still answers with its URL.
printf '%s\n' "$checked" \
    | jq -e '.event | IN("COMMENT", "APPROVE", "REQUEST_CHANGES")' > /dev/null || {
    echo "post-review.sh: $payload names no event, so the review would stay pending" >&2
    exit 2
}
printf '%s\n' "$checked" | jq -e '.body | strings | length > 0' > /dev/null || {
    echo "post-review.sh: $payload has no body, which GitHub requires" >&2
    exit 2
}
before=$(jq '.comments // [] | length' "$payload")
after=$(printf '%s\n' "$checked" | jq '.comments | length')
if [ "$before" != "$after" ]; then
    echo "post-review.sh: $((before - after)) comment(s) on #$pr fall outside the diff; run review-anchors.sh and confirm the moved text" >&2
    exit 3
fi

# Last, right before the post, so a push or a comment made while the diff was
# read is still caught. The read and the post are two calls, so a change in the
# second or so between them is not; GitHub offers no conditional post.
now=$(gh pr view "$pr" --repo "$repo" --json headRefOid,updatedAt \
    --jq '.headRefOid + " " + .updatedAt')
head=${now% *}
updated=${now#* }
if [ "$head" != "$want" ]; then
    echo "post-review.sh: #$pr moved from $want to $head; review the new head" >&2
    exit 3
fi
if [ "$updated" != "$since" ]; then
    echo "post-review.sh: #$pr was updated at $updated, not $since; reread it" >&2
    exit 3
fi

# The checked text, not the file again, so a rewrite of the file after the
# checks above cannot change what is posted.
printf '%s\n' "$checked" | gh api -X POST "repos/$repo/pulls/$pr/reviews" \
    --input - --jq .html_url
