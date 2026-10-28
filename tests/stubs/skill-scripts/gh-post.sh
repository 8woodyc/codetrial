#!/bin/sh
case "$*" in
    "pr view 5 "*) echo "${FAKE_HEAD:-h1} ${FAKE_UPDATED:-T1}" ;;
    "pr diff 5 "*) cat "$FAKE_DIFF" ;;

    # Only the checked text on stdin: posting the file again would let a rewrite
    # after the checks through.
    "api -X POST repos/sysprog21/codetrial/pulls/5/reviews --input - "*)
        jq -r '"https://github.com/sysprog21/codetrial/pull/5#pullrequestreview-" + .commit_id'
        ;;
    *)
        echo "unexpected gh call: $*" >&2
        exit 1
        ;;
esac
