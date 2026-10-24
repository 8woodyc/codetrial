#!/usr/bin/env python3
"""Build and repeat native audio onset probes in alternating queue order."""

import argparse
import csv
import hashlib
import json
import os
import pathlib
import platform
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]


def command(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, check=True, **kwargs)


def captured(args):
    return command(args, capture_output=True, text=True).stdout


def source_sha256():
    """Digest the manifests, build script, src/, and examples/."""
    paths = [ROOT / name for name in ("Cargo.toml", "Cargo.lock", "build.rs")]
    for directory in ("src", "examples"):
        paths.extend(path for path in (ROOT / directory).rglob("*") if path.is_file())
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def compiler_version(cxx):
    try:
        version = captured([cxx, "--version"])
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return version.splitlines()[0] if version else "unknown"


def write_refusal(summary, format_name, reason):
    """Keep a refused summary readable in the format its name promises."""
    if format_name == "json":
        summary.write(json.dumps({"error": reason}, indent=2) + "\n")
    elif format_name == "csv":
        writer = csv.writer(summary)
        writer.writerow(["error"])
        writer.writerow([reason])
    else:
        summary.write(reason + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("local", "room"), default="local")
    parser.add_argument("--queues", nargs="+", type=int, default=[100, 20])
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--pulses", type=int, default=20)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    # The probe and the report run from the repository root.
    args.output = args.output.resolve()
    if (
        not 1 <= args.iterations <= 100
        or not 1 <= args.pulses <= 1000
        or any(q < 10 or q > 500 or q % 10 for q in args.queues)
        or len(set(args.queues)) != len(args.queues)
    ):
        parser.error(
            "iterations 1..100, pulses 1..1000, distinct queues 10..500 in steps of 10"
        )
    if args.mode == "room" and any(
        not os.environ.get(key)
        for key in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
    ):
        parser.error(
            "room mode requires LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET"
        )
    if args.output.exists():
        parser.error(f"{args.output} already exists; each experiment needs a new one")
    env = os.environ.copy()
    if (
        platform.system() == "Linux"
        and platform.machine() == "x86_64"
        and not env.get("CXX")
    ):
        command(["sh", "scripts/fetch-clang.sh", str(ROOT / "target/clang")])
        env["CXX"] = str(ROOT / "target/clang/bin/clang++")
    # Cargo names the executable it built, wherever a configured target or
    # target directory put it, so a stale one elsewhere is never run.
    build = command(
        [
            "cargo",
            "build",
            "--locked",
            "--example",
            "audio-latency",
            "--message-format=json-render-diagnostics",
        ],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
    )
    executables = [
        message["executable"]
        for message in map(json.loads, build.stdout.splitlines())
        if message.get("reason") == "compiler-artifact"
        and message["target"]["name"] == "audio-latency"
        and message.get("executable")
    ]
    if len(executables) != 1:
        raise SystemExit("cargo build named no single audio-latency executable")
    binary = pathlib.Path(executables[0])
    # Created only once the build has succeeded, so a failed build can be rerun
    # with the same name.
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = dict(
        version=1,
        mode=args.mode,
        queues=args.queues,
        iterations=args.iterations,
        pulses=args.pulses,
        platform=platform.platform(),
        cxx=compiler_version(env.get("CXX", "c++")),
        rustc=captured(["rustc", "--version"]).strip(),
        revision=captured(["git", "rev-parse", "HEAD"]).strip(),
        dirty=bool(captured(["git", "status", "--porcelain", "--untracked-files=no"])),
        source_sha256=source_sha256(),
        binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
    )
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    results = []
    failed = False
    for iteration in range(args.iterations):
        queues = args.queues if iteration % 2 == 0 else list(reversed(args.queues))
        for queue in queues:
            output = args.output / f"iter-{iteration + 1:02d}-queue-{queue}.json"
            print(
                f"{args.mode}: iteration={iteration + 1} queue_ms={queue}", flush=True
            )
            started = time.monotonic()
            try:
                command(
                    [str(binary), args.mode, str(queue), str(args.pulses), str(output)],
                    timeout=(args.pulses + 1) * 3 + 120,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                failed = True
            if output.exists():
                results.append(str(output))
            else:
                failed = True
            print(f"elapsed_s={time.monotonic() - started:.1f}", flush=True)
    # Also with no result at all: the refusal then says so in every summary.
    refusal = ""
    for format_name in ("text", "json", "csv"):
        with (args.output / f"summary.{format_name}").open(
            "w", encoding="utf-8"
        ) as summary:
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audio-latency-report.py",
                    "--format",
                    format_name,
                    "--expected-runs",
                    str(args.iterations * len(args.queues)),
                    *results,
                ],
                cwd=ROOT,
                stdout=summary,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            # A refused experiment keeps its reason beside the raw trials
            # instead of leaving an empty summary.
            if result.stderr.strip():
                write_refusal(summary, format_name, result.stderr.strip())
                refusal = result.stderr
            failed |= result.returncode != 0
    # Every format refuses for the same reason; it is reported once.
    sys.stderr.write(refusal)
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
