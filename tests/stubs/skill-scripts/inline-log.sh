#!/bin/sh
# A stand-in for post-inline.sh: logs its arguments to $FAKE_INLINE_LOG, then
# exits with $FAKE_INLINE_RC.
echo "$@" >> "$FAKE_INLINE_LOG"
exit "${FAKE_INLINE_RC:-0}"
