#!/bin/sh
case "$*" in
    "api user --jq .login") echo viewer ;;
    "api repos/sysprog21/codetrial/issues/5/comments --paginate") echo '[]' ;;
    # FAKE_PUSHED_AFTER names a file the first post creates: a push mid-way.
    "pr view 5 "*)
        if [ -e "${FAKE_PUSHED_AFTER:-/nonexistent}" ]; then
            echo h2
        else
            echo "${FAKE_HEAD:-h1}"
        fi
        ;;
    # A second read of the diff fails: the anchors and the spans share one.
    "pr diff 5 "*)
        [ ! -e "$FAKE_DIFF.read" ] || {
            echo "diff read twice" >&2
            exit 1
        }
        : > "$FAKE_DIFF.read"
        cat "$FAKE_DIFF"
        ;;
    "api repos/sysprog21/codetrial/pulls/5/comments --paginate") cat "$FAKE_THREADS" ;;
    # A review is created whole or refused whole, as GitHub does.
    "api repos/sysprog21/codetrial/pulls/5/reviews --input "*)
        [ -z "${FAKE_REFUSE_REVIEW:-}" ] || exit 1
        # GitHub has no quote field; a draft's own fields stay home.
        ! jq -e 'any(.comments[]; has("quote") or has("moved"))' "$4" > /dev/null || {
            echo "draft field posted" >&2
            exit 1
        }
        # FAKE_LOSE_REVIEW: the review lands, and its answer is lost.
        if [ -n "${FAKE_LOSE_REVIEW:-}" ]; then
            jq --slurpfile r "$4" '. + [$r[0].comments[]
        | {user: {login: "viewer"}, commit_id: $r[0].commit_id, path, line, body}]' \
                "$FAKE_THREADS" > "$FAKE_THREADS.new" && mv "$FAKE_THREADS.new" "$FAKE_THREADS"
            exit 1
        fi
        ! jq -e --arg l "${FAKE_REFUSE_LINE:-}" 'any(.comments[]; (.line | tostring) == $l)' \
            "$4" > /dev/null || exit 1
        jq -r '.commit_id as $c | .comments[] | "posted \(.path):\(.line) at \($c)"' "$4"
        ;;
    "api repos/sysprog21/codetrial/pulls/5/comments --input "*)
        [ "$(jq -r .line "$4")" != "${FAKE_REFUSE_LINE:-}" ] || exit 1
        [ -z "${FAKE_PUSHED_AFTER:-}" ] || : > "$FAKE_PUSHED_AFTER"
        jq -r '"posted \(.path):\(.line) at \(.commit_id)"' "$4"
        ;;
    "pr comment 5 "*) echo "summary posted" ;;
    *)
        echo "unexpected gh call: $*" >&2
        exit 1
        ;;
esac
