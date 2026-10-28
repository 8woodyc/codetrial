#!/bin/sh
# A stand-in for ci-failure.sh that counts its runs in $FAKE_DIR/runs, then
# prints $FAKE_DIR/out, or fails when FAKE_CI_FAILS is set.
echo x >> "$FAKE_DIR/runs"
[ -z "${FAKE_CI_FAILS:-}" ] || exit 1
cat "$FAKE_DIR/out"
