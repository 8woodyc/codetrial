#!/usr/bin/env python3
"""Check that a request routes to the skill that owns it.

The `description` in each `.claude/skills/*/SKILL.md` is the only part of a
skill loaded before it fires, so two descriptions claiming the same phrase, or
none claiming a phrase people type, sends the agent to the wrong instructions
with no error anywhere. This runs each request in
`scripts/skill-routing-queries.json` through `claude -p` and records which
skill fired first; `expect` lists every acceptable skill, and an empty list is
a near-miss that must fire none.

    scripts/check-skill-routing.py                 # every query, twice
    scripts/check-skill-routing.py --skill codetrial-web --skill codetrial-livekit
    scripts/check-skill-routing.py --only 3,7 --runs 3

Outside the gate: it needs a logged-in `claude` and spends model calls. The
method is the trigger eval in LiveKit's agent-skills repository.

Each run works in a throwaway copy of the files git tracks or would track, so
ignored credentials (`config/*.env.*`) and build output never reach it, with
`.claude/` reduced to the skills so no project hook or setting runs. The tool
allowlist is the guarantee: with no shell, a run cannot post or spend. The
dummy credentials are insurance for a future allowlist that has one.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUERIES = os.path.join(ROOT, "scripts", "skill-routing-queries.json")
TOOLS = "Skill,Read,Glob,Grep"
WORKERS = 8
TIMEOUT = 90
SAFE_ENV = {
    "LIVEKIT_URL": "http://127.0.0.1:1",
    "LIVEKIT_API_KEY": "routing-check",
    "LIVEKIT_API_SECRET": "routing-check-routing-check-routing-check",
    "GOOGLE_API_KEY": "routing-check",
    "GH_TOKEN": "routing-check",
    "GITHUB_TOKEN": "routing-check",
}
READ_SKILL = re.compile(r"\.claude/skills/([a-z0-9-]+)/")


def fixture_names():
    """The files a run sees: tracked or trackable, `.claude/` cut to skills."""
    names = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    return [
        name
        for name in names.split("\0")
        if name
        and (name.startswith(".claude/skills/") or not name.startswith(".claude/"))
        and os.path.isfile(os.path.join(ROOT, name))
    ]


def build_fixture():
    """Copy fixture_names() to a temp tree; return it and its skills."""
    fixture = tempfile.mkdtemp(prefix="skill-routing-")
    for name in fixture_names():
        dst = os.path.join(fixture, name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(ROOT, name), dst)
    subprocess.run(["git", "init", "-q"], cwd=fixture, check=False)
    skills_dir = os.path.join(fixture, ".claude", "skills")
    skills = sorted(
        name
        for name in os.listdir(skills_dir)
        if os.path.isfile(os.path.join(skills_dir, name, "SKILL.md"))
    )
    return fixture, skills


def first_skill(event, skills):
    """The skill an assistant event invoked or opened, if any."""
    if event.get("type") != "assistant":
        return None
    for part in event.get("message", {}).get("content", []):
        if part.get("type") != "tool_use":
            continue
        args = part.get("input") or {}
        if part.get("name") == "Skill":
            name = str(args.get("skill", "")).split(":")[-1]
            if name in skills:
                return name
        elif part.get("name") == "Read":
            found = READ_SKILL.search(str(args.get("file_path", "")))
            if found and found.group(1) in skills:
                return found.group(1)
    return None


def run_query(query, fixture, skills, timeout=TIMEOUT, claude="claude"):
    """The first skill one run of a query fired, "none", or "error"."""
    cmd = [claude, "-p", query, "--output-format", "stream-json", "--verbose"]
    cmd += ["--no-session-persistence", "--setting-sources", "project"]
    cmd += ["--tools", TOOLS]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"} | SAFE_ENV
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=fixture,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError as error:
        print(f"cannot start {claude}: {error}", file=sys.stderr)
        return "error"
    # The first skill decides the run, so reading stops as soon as one fires; a
    # run that fires nothing is killed at the timeout, silent or not.
    timed_out = threading.Event()

    def expire():
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout, expire)
    timer.start()
    fired, finished = None, False
    try:
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            fired = first_skill(event, skills)
            if fired:
                break
            # Only a result that is not an error says the run decided nothing.
            if event.get("type") == "result":
                finished = not event.get("is_error")
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()
    if fired:
        return fired
    # A run that failed, timed out or never finished decided nothing either
    # way, and counting it as "none" would pass every near-miss in an outage.
    if timed_out.is_set() or not finished or proc.returncode != 0:
        print(
            f"run failed (exit {proc.returncode}"
            f"{', timed out' if timed_out.is_set() else ''}): "
            "not logged in, a flag this version lacks, or an API error",
            file=sys.stderr,
        )
        return "error"
    return "none"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--only", help="comma-separated query indices")
    ap.add_argument(
        "--skill",
        action="append",
        default=[],
        help="only the queries that expect this skill, plus every near-miss; "
        "repeat for its neighbors",
    )
    ap.add_argument(
        "--runs", type=int, default=2, help="runs per query; 3 when confirming a fix"
    )
    args = ap.parse_args()
    if args.runs < 1:
        sys.exit("--runs must be at least 1, or nothing is checked")

    with open(QUERIES) as f:
        queries = json.load(f)
    # A near-miss's one acceptable answer is that nothing fired.
    for q in queries:
        q["expect"] = q["expect"] or ["none"]
    picked = (
        [int(i) for i in args.only.split(",")] if args.only else range(len(queries))
    )
    if args.skill:
        # The near-misses ride along: they are what an over-claiming edit breaks.
        wanted = set(args.skill) | {"none"}
        picked = [i for i in picked if set(queries[i]["expect"]) & wanted]
    fixture, skills = build_fixture()
    named = {s for q in queries for s in q["expect"]} | set(args.skill)
    unknown = named - set(skills) - {"none"}
    if unknown:
        shutil.rmtree(fixture, ignore_errors=True)
        sys.exit(f"no such skill (in --skill or a query): {sorted(unknown)}")
    print(f"skills: {', '.join(skills)}", file=sys.stderr)

    fired = defaultdict(list)
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            runs = {
                pool.submit(run_query, queries[i]["q"], fixture, skills): i
                for i in picked
                for _ in range(args.runs)
            }
            for done in as_completed(runs):
                i = runs[done]
                fired[i].append(done.result())
                print(
                    f"[{i:02d}] fired={fired[i][-1]:22} "
                    f"expect={','.join(queries[i]['expect'])}",
                    file=sys.stderr,
                )
    finally:
        shutil.rmtree(fixture, ignore_errors=True)

    passed = sum(s in queries[i]["expect"] for i in picked for s in fired[i])
    total = sum(len(fired[i]) for i in picked)
    print(f"\n{passed}/{total} runs fired an acceptable skill")
    for i in picked:
        good = sum(s in queries[i]["expect"] for s in fired[i])
        if good < len(fired[i]):
            print(
                f"  [{i:02d}] {good}/{len(fired[i])} "
                f"expect={queries[i]['expect']} fired={fired[i]}\n"
                f"       {queries[i]['q'][:100]}"
            )
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
