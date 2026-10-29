#!/bin/sh
# gh for review-finish.sh's refine comment: the live head, the comment history
# in $FAKE_HISTORY, and a log of what a pr comment posts. $FAKE_API_FAIL and
# $FAKE_COMMENT_FAIL make those calls fail.
case "$*" in
    "pr view"*) echo "$FAKE_LIVE" ;;
    "api "*)
        [ -z "$FAKE_API_FAIL" ] || exit 1
        printf '%s\n' "$FAKE_HISTORY"
        ;;
    "pr comment"*)
        [ -z "$FAKE_COMMENT_FAIL" ] || exit 1
        for a; do last=$a; done
        echo comment >> "$FAKE_LOG"
        cat "$last" >> "$FAKE_LOG"
        ;;
esac
