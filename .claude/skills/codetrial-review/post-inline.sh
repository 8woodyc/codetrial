#!/bin/sh
# Post a sweep review's inline comments at the head they were read at, as one
# review so the author gets one notification, not one per finding. review-prs.md
# describes the draft and why a sweep posts it without a confirmation; the
# codetrial-review skill describes the finding it carries.
#
# Usage: post-inline.sh <pr> <draft.json> [repo]
#        Prints the review's URL, or each comment's when it fell back to one
#        post per finding, and on stderr each finding it dropped and why.
#        Exits 3 when the head moved, checked before anything is read and
#        again before every post, so a push during the reads posts nothing; 1
#        when GitHub refused a comment; 2 on bad arguments.
#
# The draft is {sha, comments: [{path, line?, start_line?, quote?, body}],
# summary}. review-anchors.sh places each comment, by its quote when the quote
# is found once, else by its line. Two kinds of finding are dropped rather than
# posted. One outside the diff, which GitHub refuses with 422. And one inside a
# hunk cubic already commented on at this head: that region has a review thread,
# and a second voice on it is noise, while the rest of the same file still gets
# read. Only a cubic comment made at this head claims its hunk: an outdated one
# keeps its old line number, which may now sit in another hunk. A finding the
# account already posted at this head is dropped too, so a rerun after a refused
# comment posts only what is missing.

set -eu

usage='usage: post-inline.sh <pr> <draft.json> [repo]'
pr=${1:?$usage}
draft=${2:?$usage}
repo=${3:-sysprog21/codetrial}
case $pr in
    *[!0-9]*)
        echo "post-inline.sh: $pr is not a pull request number" >&2
        exit 2
        ;;
esac
jq -se 'map(type) == ["object"]' "$draft" > /dev/null 2>&1 || {
    echo "post-inline.sh: $draft is not one JSON object" >&2
    exit 2
}
sha=$(jq -er '.sha | strings' "$draft") || {
    echo "post-inline.sh: $draft names no sha" >&2
    exit 2
}

same_head()
{
    # It runs inside the posting loop, whose stdin is the list of comments.
    now=$(gh pr view "$pr" --repo "$repo" --json headRefOid --jq .headRefOid \
        < /dev/null)
    [ "$now" = "$sha" ] && return
    echo "post-inline.sh: #$pr moved from $sha to $now; review the new head" >&2
    exit 3
}
same_head
me=$(gh api user --jq .login)

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# The diff is read once, for the anchors and for the hunk spans below; a push
# after this read is caught by same_head before anything is posted.
gh pr diff "$pr" --repo "$repo" > "$work/pr.diff" < /dev/null || {
    echo "post-inline.sh: #$pr diff could not be read; nothing posted" >&2
    exit 1
}

# Only the right side: a finding is about code the PR leaves behind.
jq --arg sha "$sha" '{commit_id: $sha, body: "", comments: [.comments[]
    | {path, line, side: "RIGHT", body}
      + (if .start_line then {start_line, start_side: "RIGHT"} else {} end)
      + (if .quote then {quote} else {} end)]}' \
    "$draft" > "$work/payload.json"
# Its counts are not wanted here, but its refusal is: shown, with its exit.
REVIEW_DIFF=$work/pr.diff sh "$(dirname "$0")/review-anchors.sh" "$pr" \
    "$work/payload.json" "$repo" 2> "$work/anchors.err" > "$work/anchored.json" || {
    status=$?
    cat "$work/anchors.err" >&2
    exit $status
}

# Each hunk's new-side span, one tab-separated "path first last" per row; a hunk
# adding nothing on the right has no span.
awk "$(cat "$(dirname "$0")/diff-path.awk")"'
    /^diff --git / { header = 1; path = ""; next }
    header && /^\+\+\+ / { path = header_path($0); next }
    /^@@ / && path != "/dev/null" {
        header = 0
        n = split(substr($3, 2), r, ",")
        count = n > 1 ? r[2] + 0 : 1
        if (count > 0) print path "\t" r[1] "\t" (r[1] + count - 1)
    }
' "$work/pr.diff" > "$work/hunks"

# Sorts the findings against the review threads as they stand, leaving those to
# post in $work/post. Run again after a refused review, since a review that
# landed with its answer lost reads as refused, and its findings are then
# already posted at this head; a second sort only ever drops more.
sort_findings()
{
    gh api "repos/$repo/pulls/$pr/comments" --paginate \
        > "$work/threads.json" < /dev/null
    jq -n --slurpfile anchored "$work/anchored.json" \
        --slurpfile threads "$work/threads.json" --arg sha "$sha" --arg me "$me" \
        --rawfile hunks "$work/hunks" '
      [$hunks | split("\n")[] | select(length > 0) | split("\t")
        | {path: .[0], first: (.[1] | tonumber), last: (.[2] | tonumber)}] as $spans
      | def span: {path, first: (.start_line // .line), last: .line};
        def overlaps($a; $b): $a.path == $b.path
          and $a.first <= $b.last and $b.first <= $a.last;
      ($threads | add // [] | map(select(.commit_id == $sha))) as $here
      | [$here[] | select(.user.login == "cubic-dev-ai[bot]" and .line != null
          and .side == "RIGHT") | span] as $cubic
      | def posted: . as $c | any($here[]; .user.login == $me
          and .path == $c.path and .line == $c.line and .body == $c.body);
      def claimed: span as $s
          | any($spans[]; . as $h | overlaps($h; $s) and any($cubic[]; overlaps($h; .)));
      ($anchored[0].moved[] | {drop: "outside the diff"} + .),
      ($anchored[0].comments[]
        | if posted then {drop: "already posted at this head"} + .
          elif claimed then {drop: "in a hunk cubic already commented on"} + .
          else . end)
    ' > "$work/sorted.json"
    jq -c 'select(.drop | not)' "$work/sorted.json" > "$work/post"
}
sort_findings

# GitHub creates a review whole or not at all, so one refused comment refuses
# them all; only then is each posted on its own, so the rest still land.
status=0
if [ -s "$work/post" ]; then
    same_head
    jq -s --arg sha "$sha" '{commit_id: $sha, event: "COMMENT", body: "",
        comments: .}' "$work/post" > "$work/review.json"
    if gh api "repos/$repo/pulls/$pr/reviews" --input "$work/review.json" \
        --jq .html_url < /dev/null 2> "$work/refused"; then
        : > "$work/post"
    else
        why=$(head -1 "$work/refused")
        echo "post-inline.sh: #$pr refused the review${why:+ ($why)};" \
            "posting each finding" >&2
        sort_findings
    fi
fi
jq -r 'select(.drop) | "post-inline.sh: #'"$pr"' dropped \(.path):\(.line // "unplaced"), \(.drop)"' \
    "$work/sorted.json" >&2
while IFS= read -r comment; do

    # Before every write: a push mid-way leaves what already landed, which a
    # rerun at the new head reads as not current, and posts nothing more.
    same_head
    printf '%s\n' "$comment" | jq --arg sha "$sha" '. + {commit_id: $sha}' \
        > "$work/one.json"
    gh api "repos/$repo/pulls/$pr/comments" --input "$work/one.json" \
        --jq .html_url < /dev/null || {
        echo "post-inline.sh: #$pr refused $(jq -r '"\(.path):\(.line)"' "$work/one.json")" >&2
        status=1
    }
done < "$work/post"

# The summary is a plain PR comment, so the rerun check reads those. It points
# at the inline detail, so it waits until every finding it may name is on the
# thread: none refused, none dropped unless already posted, and at least one.
summary=$(jq -r '.summary // empty' "$draft")
landed=$(jq -s --argjson status "$status" '
    if $status != 0 or any(.[]; .drop and .drop != "already posted at this head")
    then 0 else length end' "$work/sorted.json")
if [ -n "$summary" ] && [ "$landed" -eq 0 ]; then
    echo "post-inline.sh: #$pr summary held back: its findings did not all land" >&2
elif [ -n "$summary" ]; then
    gh api "repos/$repo/issues/$pr/comments" --paginate > "$work/said.json"
    if jq -se --arg me "$me" --arg text "$summary" 'add // [] | any(.[];
        .user.login == $me and (.body | rtrimstr("\n")) == $text)' \
        "$work/said.json" > /dev/null; then
        echo "post-inline.sh: #$pr summary already posted" >&2
    else
        same_head
        printf '%s\n' "$summary" > "$work/summary.md"
        gh pr comment "$pr" --repo "$repo" --body-file "$work/summary.md"
    fi
fi
exit $status
