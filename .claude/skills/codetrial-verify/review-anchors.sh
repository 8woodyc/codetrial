#!/bin/sh
# Check a drafted review payload against the pull request's diff before it is
# posted. GitHub refuses the whole review with 422 when one inline comment names
# a line outside every hunk, so each comment whose path, side and line the diff
# does not show is moved into the review body as "path:line: text", and the rest
# stay inline. Writes nothing to GitHub.
#
# Usage: review-anchors.sh <pr> <payload.json> [repo]
#        Prints the checked payload; the counts go to stderr.
#
# The payload is the one review.md describes: event, commit_id, body, and
# comments, each with path, line, side (RIGHT for an added or unchanged line,
# LEFT for a deleted one) and body.

set -eu

pr=${1:?usage: review-anchors.sh <pr> <payload.json> [repo]}
payload=${2:?usage: review-anchors.sh <pr> <payload.json> [repo]}
repo=${3:-sysprog21/codetrial}
case $pr in
    *[!0-9]*)
        echo "review-anchors.sh: $pr is not a pull request number" >&2
        exit 2
        ;;
esac
[ -r "$payload" ] || {
    echo "review-anchors.sh: cannot read $payload" >&2
    exit 2
}

# Every line a comment may anchor to, one "path side line" per row: a context
# line on both sides, an added line on the right, a deleted line on the left.
# The --- and +++ lines are headers only before a file's first hunk, since a
# deleted line reading "-- x" shows as "--- x" inside one; a deleted file's path
# comes from its --- line. The diff is read before awk sees it: in a pipe set -e
# sees only the last command, and a refused diff (GitHub declines one past 20000
# lines) would read as no anchors and move every comment into the body. The
# anchors reach jq on stdin rather than as an argument, which a 20000-line diff
# would push past the argument limit.
diff=$(gh pr diff "$pr" --repo "$repo")
printf '%s\n' "$diff" | awk '
    # A header path: drop the tab git appends after a name with a space, the
    # quotes it puts around one with special characters, and the a/ or b/.
    function header_path(line,    p) {
        p = substr(line, 5)
        sub(/\t.*$/, "", p)
        if (p ~ /^".*"$/) p = substr(p, 2, length(p) - 2)
        return p == "/dev/null" ? p : substr(p, 3)
    }
    /^diff --git / { header = 1; path = ""; next }
    header && /^--- / { old = header_path($0); next }
    header && /^\+\+\+ / { path = header_path($0); if (path == "/dev/null") path = old; next }
    /^@@ / {
        header = 0
        split($2, l, ","); split($3, r, ",")
        left = substr(l[1], 2) + 0; right = substr(r[1], 2) + 0
        next
    }
    header || path == "" { next }
    /^\\/ { next }
    /^\+/ { print path " RIGHT " right++; next }
    /^-/ { print path " LEFT " left++; next }
    { print path " RIGHT " right++; print path " LEFT " left++ }
' | jq --rawfile anchors /dev/stdin '
  ([$anchors | split("\n")[] | select(length > 0) | {(.): true}] | add // {}) as $ok
  | def key: "\(.path) \(.side // "RIGHT") \(.line)";
  # A multi-line comment is refused when its first line is outside a hunk too.
  def start: .start_line == null
    or $ok["\(.path) \(.start_side // .side // "RIGHT") \(.start_line)"];
  def anchored: $ok[key] and start;
  (.comments // []) as $all
  | [$all[] | select(anchored)] as $inline
  | [$all[] | select(anchored | not)] as $moved
  | .comments = $inline
  | .body = ([.body] + [$moved[] | "\(.path):\(.line): \(.body)"]
      | map(select(. != null and . != "")) | join("\n\n"))
  | ("inline \($inline | length), moved to body \($moved | length)\n" | stderr
      | empty), .
' "$payload"
