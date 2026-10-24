#!/bin/sh
# Check a drafted review payload against the pull request's diff before it is
# posted. GitHub refuses the whole review with 422 when one inline comment names
# a line outside every hunk, so each comment whose path, side and line the diff
# does not show is moved out of comments: into moved, which the posting scripts
# act on, and into the review body as "path:line: text", which is how the user
# reads it before confirming. Writes nothing to GitHub.
#
# Usage: review-anchors.sh <pr> <payload.json> [repo]
#        Prints the checked payload; the counts go to stderr. REVIEW_DIFF names
#        a file holding the PR's diff, already read at the head the payload
#        names, so a caller that needs the diff too reads it once.
#
# The payload is the one the codetrial-review skill describes: event, commit_id,
# body, and comments, each with path, line, side (RIGHT for an added or
# unchanged line, LEFT for a deleted one) and body. A comment may carry quote
# instead of, or beside, line: the code it is about, copied verbatim from the
# head (or from the base, for a LEFT comment). A reviewer quotes code far more
# reliably than it counts lines, so a quote found in its file's hunks sets line
# (and start_line when it spans several); requote below says exactly when. A
# quote that places nothing moves its comment, whatever line came with it: the
# line was the guess the quote exists to replace, and the sweep posts unread.
# Other fields pass through untouched; quote does not, since GitHub has no such
# field.

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

# Slurped, so an empty file fails too: jq reads it as no input, and every later
# read of the payload would print nothing and succeed.
jq -se 'map(type) == ["object"]' "$payload" > /dev/null 2>&1 || {
    echo "review-anchors.sh: $payload is not one JSON object" >&2
    exit 2
}

# Every line a comment may anchor to, one tab-separated "path side line hunk
# text" per row: a context line on both sides, an added line on the right, a
# deleted line on the left. The hunk number keeps a quote from matching across
# two hunks, and text, the line without its diff marker, is what a quote is
# matched against. The --- and +++ lines are headers only before a file's first
# hunk, since a deleted line reading "-- x" shows as "--- x" inside one; a
# deleted file's path comes from its --- line. The diff is read before awk sees
# it: in a pipe set -e sees only the last command, and a refused diff (GitHub
# declines one past 20000 lines) would read as no anchors and move every comment
# into the body. The anchors reach jq on stdin rather than as an argument, which
# a 20000-line diff would push past the argument limit.
if [ -n "${REVIEW_DIFF:-}" ]; then
    diff=$(cat "$REVIEW_DIFF")
else
    diff=$(gh pr diff "$pr" --repo "$repo")
fi
checked=$(printf '%s\n' "$diff" | awk "$(cat "$(dirname "$0")/diff-path.awk")"'
    /^diff --git / { header = 1; path = ""; next }
    header && /^--- / { old = header_path($0); next }
    header && /^\+\+\+ / { path = header_path($0); if (path == "/dev/null") path = old; next }
    /^@@ / {
        header = 0; hunk++
        split($2, l, ","); split($3, r, ",")
        left = substr(l[1], 2) + 0; right = substr(r[1], 2) + 0
        next
    }
    header || path == "" { next }
    /^\\/ { next }
    { text = substr($0, 2) }
    /^\+/ { print path "\tRIGHT\t" right++ "\t" hunk "\t" text; next }
    /^-/ { print path "\tLEFT\t" left++ "\t" hunk "\t" text; next }
    { print path "\tRIGHT\t" right++ "\t" hunk "\t" text
      print path "\tLEFT\t" left++ "\t" hunk "\t" text }
' | jq --rawfile anchors /dev/stdin '
  def trim: gsub("^\\s+|\\s+$"; "");
  # Each row trimmed once here, since a quote is compared against many windows.
  [$anchors | split("\n")[] | select(length > 0) | split("\t")
    | {path: .[0], side: .[1], line: (.[2] | tonumber), hunk: .[3],
       text: (.[4:] | join("\t") | trim)}] as $rows
  | ([$rows[] | {("\(.path) \(.side) \(.line)"): true}] | add // {}) as $ok
  # The non-blank lines of each file and side, in diff order.
  | ($rows | map(select(.text != "")) | group_by(.path + " " + .side)
      | map({(.[0].path + " " + .[0].side): .}) | add // {}) as $lines
  # Where a quote sits: every run of consecutive lines in one hunk whose text
  # equals the quote'"'"'s non-blank lines, trimmed.
  | def found($at; $quote):
    ($quote | split("\n") | map(trim) | map(select(length > 0))) as $want
    | ($lines[$at] // []) as $l
    | if ($want | length) == 0 then []
      else [range(0; ($l | length) - ($want | length) + 1) as $i
        | select($l[$i].text == $want[0])
        | $l[$i:$i + ($want | length)]
        | select(all(.[]; .hunk == $l[$i].hunk))
        | select(map(.text) == $want)
        | {first: .[0].line, last: .[-1].line}]
      end;
  # A blank quote names no code, so the line given with it stands.
  def requote: if (.quote | type) != "string" or (.quote | test("\\S") | not)
    then del(.quote)
    else (.side // "RIGHT") as $side | .line as $given
      | [found(.path + " " + $side; .quote)[]] as $all
      # One match corrects a miscounted line, but not a line outside every
      # hunk: that comment is about unchanged code, and a quote as plain as a
      # closing brace could carry it to an unrelated line of the diff.
      | ($given == null or $ok["\(.path) \($side) \($given)"]) as $inside
      | (if ($all | length) == 1 and $inside then $all
         else [$all[] | select($given != null and .first <= $given and $given <= .last)]
         end) as $at
      | del(.quote, .start_line, .start_side)
      | if ($at | length) == 1 then
          . + {line: $at[0].last, side: $side}
          + (if $at[0].first < $at[0].last
             then {start_line: $at[0].first, start_side: $side} else {} end)
        else . + {line: null} end
    end;
  def key: "\(.path) \(.side // "RIGHT") \(.line)";
  # A multi-line comment is refused when its first line is outside a hunk too.
  def start: .start_line == null
    or $ok["\(.path) \(.start_side // .side // "RIGHT") \(.start_line)"];
  def anchored: .line != null and $ok[key] and start;
  [(.comments // [])[] | requote] as $all
  | .comments = [$all[] | select(anchored)]
  | .moved = [$all[] | select(anchored | not)]
  | .body = ([.body] + [.moved[]
      | if .line == null then "\(.path): \(.body)" else "\(.path):\(.line): \(.body)" end]
      | map(select(. != null and . != "")) | join("\n\n"))
' "$payload")
printf '%s\n' "$checked"
# As plain text, since jq's own stderr prints a JSON string on older jq.
printf '%s\n' "$checked" | jq -r '"inline \(.comments | length), moved \(.moved | length)"' >&2
