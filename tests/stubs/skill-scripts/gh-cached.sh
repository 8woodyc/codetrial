#!/bin/sh
# gh for ci-cached.sh: the check list is $FAKE_DIR/checks and anything else, the
# head lookup, answers with $FAKE_DIR/head.
case "$*" in
    "pr checks"*) cat "$FAKE_DIR/checks" ;;
    *) cat "$FAKE_DIR/head" ;;
esac
