#!/bin/sh
case "$*" in
    "api user --jq .login") echo viewer ;;
    *collaborators*) printf 'maint\nviewer\n' ;;
    *actions/runs*) cat "$FAKE_RUNS" ;;
    "api graphql --paginate"*) cat "$FAKE_PAGE" ;;
    "run list "*)
        [ -n "${FAKE_MAIN_FAILED:-}" ] || exit 1
        echo 500
        ;;
    "run view 500 "*) printf '%s\n' "$FAKE_MAIN_FAILED" ;;
    "api graphql"*) cat "$FAKE_AGAIN" ;;
    "pr view 404 "*)
        echo "no such pull request" >&2
        exit 1
        ;;
    # FAKE_DIFFS maps a PR number to its diff; a PR missing from it fails.
    "pr diff "*)
        [ -n "${FAKE_DIFFS:-}" ] || exit 1
        jq -jre --arg n "$3" '.[$n] | strings' "$FAKE_DIFFS"
        ;;
    "pr view"*) printf '{"pr":%s,"state":"MERGED","merge":"m%s"}\n' "$3" "$3" ;;
    *)
        echo "unexpected gh call: $*" >&2
        exit 1
        ;;
esac
