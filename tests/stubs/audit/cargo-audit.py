#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path

args = sys.argv
db = Path(args[args.index("--db") + 1])
with open(os.environ["AUDIT_CALLS"], "a") as log:
    log.write(json.dumps(args[1:]) + "\n")
if "--no-fetch" not in args:
    db.mkdir()
    subprocess.run(["git", "init", "--quiet", str(db)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(db),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.org",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            "snapshot",
        ],
        check=True,
    )
data = json.loads(Path(args[args.index("--file") + 1]).read_text())
if "raw" in data:
    print(data["raw"])
    sys.exit(data.get("exit", 1))
items = [
    {"advisory": {"id": a}, "package": {"name": n, "version": v}}
    for a, n, v in data["findings"]
]
print(
    json.dumps(
        {
            "vulnerabilities": {
                "found": bool(items),
                "count": len(items),
                "list": items,
            },
            "warnings": data.get("warnings", {}),
        }
    )
)
sys.exit(data.get("exit", int(bool(items))))
