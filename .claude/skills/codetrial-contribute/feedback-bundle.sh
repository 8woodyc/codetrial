#!/bin/sh
# Read-only: every review request on one pull request, complete. It is the fetch
# review-feedback.md describes, in one call: top-level reviews and comments,
# every review thread with all of its replies (a thread cut at 100 comments is
# refetched whole), and the open requests listed first.
#
# Usage: feedback-bundle.sh <pr> [repo]
#        Prints {number, url, head, reviews, comments, threads, open,
#        complete}. open holds the unresolved threads; complete is false when
#        a listing could not be read to its end, and then nothing may be
#        reported as addressed. Exits 1 when GitHub could not be read at all
#        (a failed read is never an empty PR), 2 on bad arguments.

# shellcheck disable=SC2016  # the $-names in the queries are GraphQL variables
set -eu

usage='usage: feedback-bundle.sh <pr> [repo]'
[ $# -ge 1 ] || {
    echo "$usage" >&2
    exit 2
}
pr=$1
repo=${2:-sysprog21/codetrial}
case $pr in
    *[!0-9]*)
        echo "feedback-bundle.sh: $pr is not a pull request number" >&2
        exit 2
        ;;
esac
owner=${repo%/*}
name=${repo#*/}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

gh auth status > /dev/null 2>&1 || {
    echo "feedback-bundle.sh: gh is not signed in; the feedback is unread, not empty" >&2
    exit 1
}
gh pr view "$pr" --repo "$repo" --json number,url,headRefOid,comments,reviews \
    > "$work/pr.json"
gh api graphql --paginate -F n="$pr" -F owner="$owner" -F name="$name" -f query='
  query($n: Int!, $owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) { pullRequest(number: $n) {
    reviewThreads(first: 100, after: $endCursor) {
      pageInfo { hasNextPage endCursor }
      nodes { id isResolved isOutdated path line originalLine
        comments(first: 100) {
          pageInfo { hasNextPage }
          nodes { id author { login } body url } } } } } } }' \
    > "$work/threads.raw"
# Read through a file, not a pipe: without pipefail a failed gh would pass.
jq -s '[.[].data.repository.pullRequest.reviewThreads.nodes[]]' \
    "$work/threads.raw" > "$work/threads.json"

# A thread whose replies were cut is refetched whole; one that cannot be is
# marked, so the bundle says it is incomplete instead of hiding the cut.
refetch_thread()
{
    gh api graphql --paginate -F thread="$1" -f query='
      query($thread: ID!, $endCursor: String) {
      node(id: $thread) { ... on PullRequestReviewThread {
        comments(first: 100, after: $endCursor) {
          pageInfo { hasNextPage endCursor }
          nodes { id author { login } body url } } } } }' \
        > "$work/full-$1.raw" \
        && jq -s '[.[].data.node.comments.nodes[]]' "$work/full-$1.raw" \
            > "$work/full-$1.json"
}
jq -r '.[] | select(.comments.pageInfo.hasNextPage) | .id' "$work/threads.json" \
    | while read -r thread; do
        refetch_thread "$thread" || rm -f "$work/full-$thread.json"
    done

# The refetched threads travel as a file: a thread cut at 100 comments is the
# large case, and an argument past the OS limit would stop jq from starting.
for f in "$work"/full-*.json; do
    [ -f "$f" ] || continue
    id=${f##*/full-}
    jq -c --arg id "${id%.json}" '{($id): .}' "$f"
done | jq -s 'add // {}' > "$work/full.json"

jq -n --slurpfile pr "$work/pr.json" --slurpfile threads "$work/threads.json" \
    --slurpfile full "$work/full.json" '
  $threads[0] as $t | $full[0] as $f
  | [$t[] | . as $x | {id, isResolved, isOutdated, path, line, originalLine,
      comments: ($f[$x.id] // $x.comments.nodes),
      complete: ((.comments.pageInfo.hasNextPage | not) or ($f[$x.id] != null))}]
    as $threads
  | {number: $pr[0].number, url: $pr[0].url, head: $pr[0].headRefOid,
     reviews: $pr[0].reviews, comments: $pr[0].comments, threads: $threads,
     open: [$threads[] | select(.isResolved | not)],
     complete: all($threads[]; .complete)}'
