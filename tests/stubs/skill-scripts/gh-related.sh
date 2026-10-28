#!/bin/sh
case "$*" in
    "api user --jq .login") echo viewer ;;
    "issue view 5 "*) echo '{"number":5,"title":"t","state":"OPEN","stateReason":"","assignees":["viewer","someone"]}' ;;
    "issue view "*) printf '{"number":%s,"title":"t","state":"OPEN","stateReason":"","assignees":[]}\n' "$3" ;;
    "pr list"*) cat "$FAKE_PRS" ;;
    *)
        echo "unexpected gh call: $*" >&2
        exit 1
        ;;
esac
