#!/bin/sh
# gh for feedback-bundle.sh: FAKE_NO_AUTH fails the sign-in check and
# FAKE_NO_THREAD fails the refetch of a thread cut at 100 comments.
case "$*" in
    "auth status") [ -z "${FAKE_NO_AUTH:-}" ] || exit 1 ;;
    "pr view"*) cat "$FAKE_DIR/pr.json" ;;
    *"thread="*)
        [ -z "${FAKE_NO_THREAD:-}" ] || exit 1
        cat "$FAKE_DIR/thread.json"
        ;;
    *) cat "$FAKE_DIR/threads.json" ;;
esac
