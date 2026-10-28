#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
path = args[1]
data = json.load(open(os.environ["FAKE_GH_DATA"]))
if args[:2] == ["attestation", "verify"]:
    if "--help" in args:
        print(
            "--signer-workflow --source-ref --source-digest --deny-self-hosted-runners"
        )
        sys.exit(0)
    with open(os.environ["FAKE_GH_DATA"] + ".calls", "a") as log:
        log.write(" ".join(args) + "\n")
    sys.exit(1 if data.get("_attestation_fail") else 0)
key = path.split("?")[0]
tail = key.split("/", 3)[-1]
order = "sort=updated&direction=asc"
needs = {
    "issues": ["state=all", order],
    "issues/comments": [order],
    "pulls/comments": [order],
    "collaborators": ["permission=push"],
}
for need in needs.get(tail, []):
    if need not in path:
        sys.stderr.write("HTTP 422: the request lost " + need + "\n")
        sys.exit(1)
if "since=" in path and key + "?since" in data:
    key += "?since"
with open(os.environ["FAKE_GH_DATA"] + ".calls", "a") as log:
    log.write(path + "\n")
if key not in data:
    sys.stderr.write("HTTP 404: Not Found (" + path + ")\n")
    sys.exit(1)
# An answer {"fail": "<first stderr line>"} is a failed call, as a 410 or 502.
if isinstance(data[key], dict) and set(data[key]) == {"fail"}:
    sys.stderr.write(data[key]["fail"] + "\n")
    sys.exit(1)
print(json.dumps(data[key]))
