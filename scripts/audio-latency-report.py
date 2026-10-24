#!/usr/bin/env python3
"""Summarize synthetic audio probe runs without hiding failed deliveries."""

import argparse
import csv
import json
import math
import statistics
import sys
from collections import defaultdict


def percentile(ordered, fraction):
    """Interpolate between ranks of already sorted values."""
    rank = (len(ordered) - 1) * fraction
    low = math.floor(rank)
    high = math.ceil(rank)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def summarize(runs, expected_runs=None):
    if expected_runs is not None and len(runs) != expected_runs:
        raise ValueError(
            f"expected {expected_runs} runs, got {len(runs)}: a trial left no result"
        )
    if not runs:
        raise ValueError("no runs")
    groups = defaultdict(list)
    seen = set()
    for run in runs:
        if not isinstance(run, dict) or not {
            "error",
            "teardown_complete",
            "teardown_errors",
        }.issubset(run):
            raise ValueError("missing probe lifecycle fields")
        identity = json.dumps(run, sort_keys=True)
        if identity in seen:
            raise ValueError("the same run was given twice")
        seen.add(identity)
        if (
            run.get("version") != 3
            or run.get("mode") not in ("local", "room")
            or type(run.get("queue_ms")) is not int
            or run["queue_ms"] < 10
            or run["queue_ms"] > 500
            or run["queue_ms"] % 10
            or run.get("sample_rate") != 24000
            or run.get("frame_ms") != 10
            or run.get("burst_ms") != 100
            or run.get("delivery_window_ms") != 1000
            or type(run.get("requested_pulses")) is not int
            or not 1 <= run["requested_pulses"] <= 1000
        ):
            raise ValueError("unsupported probe configuration")
        if run["teardown_complete"] is not True or run["teardown_errors"] != []:
            raise ValueError("probe teardown failed or did not complete")
        if run["error"] is not None:
            raise ValueError(f"probe stopped early: {run['error']}")
        samples = run.get("samples")
        if not isinstance(samples, list) or len(samples) != run["requested_pulses"] + 1:
            raise ValueError(
                "run must include warmup and every requested measurement sample"
            )
        previous_sent = -1
        for index, sample in enumerate(samples):
            if not isinstance(sample, dict) or not {
                "sent_ms",
                "capture_ms",
                "delivery_ms",
                "onsets",
                "late_onsets",
            }.issubset(sample):
                raise ValueError("missing sample fields")
            if (
                type(sample.get("sequence")) is not int
                or sample.get("sequence") != index
                or sample.get("warmup") is not (index == 0)
            ):
                raise ValueError("invalid sequence or warmup marker")
            for field in ("sent_ms", "capture_ms", "delivery_ms"):
                value = sample[field]
                if field == "delivery_ms" and value is None:
                    continue
                if (
                    type(value) not in (int, float)
                    or not 0 <= value <= 2**53 - 1
                    or not math.isfinite(value)
                ):
                    raise ValueError(f"invalid {field}")
            if sample["sent_ms"] <= previous_sent:
                raise ValueError("pulse sends must be strictly increasing")
            previous_sent = sample["sent_ms"]
            if (
                sample["delivery_ms"] is not None
                and sample["delivery_ms"] >= run["delivery_window_ms"]
            ):
                raise ValueError("delivery is outside its measurement window")
            for field in ("onsets", "late_onsets"):
                if type(sample[field]) is not int or sample[field] < 0:
                    raise ValueError(f"invalid {field} count")
            onsets, late = sample["onsets"], sample["late_onsets"]
            if (onsets == 1 and late == 0) != (sample["delivery_ms"] is not None):
                raise ValueError("delivery timing disagrees with onset count")
        for before, after in zip(samples, samples[1:]):
            if (
                before["delivery_ms"] is not None
                and before["delivery_ms"] >= after["sent_ms"] - before["sent_ms"]
            ):
                raise ValueError("delivery overlaps the next pulse window")
        groups[(run["mode"], run["queue_ms"])].append(samples)
    if not groups:
        raise ValueError("no runs")
    rows = []
    for (mode, queue), trials in sorted(groups.items()):
        samples = [sample for trial in trials for sample in trial[1:]]
        delivered = [s["delivery_ms"] for s in samples if s["delivery_ms"] is not None]
        row = dict(
            mode=mode,
            queue_ms=queue,
            runs=len(trials),
            attempted=len(samples),
            delivered=len(delivered),
            missing=len(samples) - len(delivered),
            late=sum(s["late_onsets"] > 0 for s in samples),
            # A delivery slower than the window lands in the next pulse's, so
            # the only trace of a constant overrun is the warmup going missing.
            warmup_missing=sum(trial[0]["delivery_ms"] is None for trial in trials),
            small_n=len(delivered) < 20,
        )
        for name, values in (
            ("delivery", sorted(delivered)),
            ("capture", sorted(s["capture_ms"] for s in samples)),
        ):
            stats = (
                {
                    "mean": statistics.mean(values),
                    "p50": percentile(values, 0.50),
                    "p95": percentile(values, 0.95),
                    "p99": percentile(values, 0.99),
                    "max": values[-1],
                }
                if values
                else dict.fromkeys(("mean", "p50", "p95", "p99", "max"))
            )
            row.update({f"{name}_{stat}_ms": value for stat, value in stats.items()})
        rows.append(row)
    return rows


def rounded(row):
    """Round for display only; budgets compare the unrounded statistics."""
    return {
        key: round(value, 3) if type(value) is float else value
        for key, value in row.items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # Zero files is accepted so a runner whose every trial left no result
    # still gets a refusal to write in place of statistics.
    parser.add_argument("files", nargs="*")
    parser.add_argument("--format", choices=("text", "json", "csv"), default="text")
    parser.add_argument("--max-p95-ms", type=float)
    parser.add_argument("--max-missing-rate", type=float, default=0)
    parser.add_argument(
        "--expected-runs",
        type=int,
        help="refuse unless exactly this many run files are given",
    )
    args = parser.parse_args()
    if not 0 <= args.max_missing_rate <= 1 or (
        args.max_p95_ms is not None
        and (not math.isfinite(args.max_p95_ms) or args.max_p95_ms <= 0)
    ):
        parser.error("invalid latency or missing-rate threshold")
    try:
        runs = []
        for path in args.files:
            with open(path, encoding="utf-8") as source:
                runs.append(json.load(source))
        rows = summarize(runs, args.expected_runs)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        parser.exit(2, f"invalid probe result: {error}\n")
    shown = [rounded(row) for row in rows]
    if args.format == "json":
        print(json.dumps(shown, indent=2, allow_nan=False))
    elif args.format == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=list(shown[0]))
        writer.writeheader()
        writer.writerows(shown)
    else:
        for row in shown:
            print(" ".join(f"{key}={value}" for key, value in row.items()))
    return int(
        any(
            row["delivered"] == 0
            or row["warmup_missing"]
            or row["missing"] / row["attempted"] > args.max_missing_rate
            or (
                args.max_p95_ms is not None
                and row["delivery_p95_ms"] is not None
                and row["delivery_p95_ms"] > args.max_p95_ms
            )
            for row in rows
        )
    )


if __name__ == "__main__":
    sys.exit(main())
