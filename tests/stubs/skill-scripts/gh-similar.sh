#!/bin/sh
# gh for post-similar.sh: each PR's view is $FAKE_DIR/<pr>.json. A head lookup
# answers from $FAKE_DIR/<pr>.live when that exists, to stand for a push after
# the view was read. A comment is recorded in $FAKE_DIR/posted, and a file
# $FAKE_DIR/after-<pr>-<other> replaces <other>'s view and head once a comment
# on <pr> has posted, to stand for a push during the posts. Every call is logged
# to $FAKE_DIR/calls.
echo "$*" >> "$FAKE_DIR/calls"
case "$*" in
    "pr view "*"--jq .headRefOid")
        if [ -f "$FAKE_DIR/$3.live" ]; then
            cat "$FAKE_DIR/$3.live"
        else
            jq -r .headRefOid "$FAKE_DIR/$3.json"
        fi
        ;;
    "pr view "*) cat "$FAKE_DIR/$3.json" ;;
    "pr comment "*)
        echo "$3" >> "$FAKE_DIR/posted"
        for after in "$FAKE_DIR"/after-"$3"-*; do
            [ -f "$after" ] || continue
            cp "$after" "$FAKE_DIR/${after##*-}.json"
        done
        ;;
esac
