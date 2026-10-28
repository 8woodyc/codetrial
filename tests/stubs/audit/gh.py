#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
action = "api" if args[0] == "api" else args[1]
with open(os.environ["GH_CALLS"], "a") as log:
    log.write(json.dumps(args) + "\n")
if action == os.environ["FAIL_ACTION"]:
    sys.exit(1)
if "--slurp" in args and ("--jq" in args or "--template" in args):
    sys.exit("the `--slurp` option is not supported with `--jq` or `--template`")
if action == "api":
    comments = any(arg.endswith("/comments") for arg in args)
    print(os.environ["COMMENT_PAGES" if comments else "ISSUE_PAGES"])
if action == "view":
    print(os.environ["OLD_BODY"])
