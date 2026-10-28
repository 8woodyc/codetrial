#!/bin/sh
case "$*" in
    "pr view 5 "*)
        while [ "$1" != --jq ]; do shift; done
        pr=${FAKE_PR:-}
        [ -n "$pr" ] || pr='{"headRefOid":"h1","files":[],"changedFiles":0}'
        printf '%s\n' "$pr" | jq -c "$2"
        ;;
    "pr checks 5 "*)
        cat "$FAKE_CHECKS"
        exit 1
        ;;
    "run list "*"--event push "*) echo "500 ${FAKE_MAIN_AT:-2026-10-22T05:00:00Z}" ;;
    "run view 500 "*)
        if [ -n "${FAKE_MAIN:-}" ]; then
            printf '%s\n' "$FAKE_MAIN"
        else
            printf '%s\n' '[{"name":"check","conclusion":"failure"},{"name":"gates (portable)","conclusion":"success"}]'
        fi
        ;;
    "run view 7 --repo sysprog21/codetrial --json headSha"*) echo "h1 2026-10-22T01:00:00Z" ;;
    "run view 7 "*"--job 70 "*) cat "$FAKE_LOG" ;;
    "run view 7 "*"--job 73 "*) printf 'gates (browser)\tlobby\t2026-10-22T00:00:00.0Z SyntaxError: Unexpected token\n' ;;
    "run view 7 "*"--job 74 "*) : ;;
    "run view 7 "*"--job 75 "*)
        echo "log expired" >&2
        exit 1
        ;;
    "run view 7 "*"--job 71 "*) printf 'check\tcollect\t2026-10-22T00:00:00.0Z ##[error]gate lanes: failure\n' ;;
    "pr diff 5 "*) cat "$FAKE_DIFF" ;;
    *)
        echo "unexpected gh call: $*" >&2
        exit 1
        ;;
esac
