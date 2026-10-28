#!/bin/sh
printf '%s\n' "$*" >> "$INSTALL_LOG.release"
[ "$RELEASE_FAIL" != 1 ]
