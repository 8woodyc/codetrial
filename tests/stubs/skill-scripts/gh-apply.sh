#!/bin/sh
# gh for apply-pr-actions.sh: logs every call to $FAKE_LOG and answers the few
# reads and approval writes the script makes. FAKE_MOVED lists PRs whose live
# head differs from the sweep's.
echo "$*" >> "$FAKE_LOG"
case "$*" in
    "pr view "*"--jq .headRefOid")
        case " ${FAKE_MOVED:-} " in
            *" $3 "*) echo moved ;;
            *) echo "h$3" ;;
        esac
        ;;
    "pr view 3 "*) echo "${FAKE_VIEW:-CONFLICTING h3}" ;;
    "run view "*) echo "${FAKE_RUN_STATUS:-in_progress}" ;;
    "api -X POST repos/"*/approve)
        [ -z "${FAKE_NOT_WAITING:-}" ] && exit 0
        echo "This workflow run is not waiting for approval" >&2
        exit 1
        ;;
esac
