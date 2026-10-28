#!/bin/sh
printf '%s\n' "$*" >> "$INSTALL_LOG.cargo"
[ "$CARGO_FAIL" != 1 ]
