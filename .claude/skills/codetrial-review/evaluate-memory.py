#!/usr/bin/env python3
"""Freeze a local public snapshot and measure source-pinned historical lookups.

This tool never calls GitHub or migrates an evaluation database. The explicitly
selected local Git commit supplies executable control code; artifacts supply
only data. Results with model-authored known positives are not human judgments.
"""

import argparse
import contextlib
import fcntl
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
RUNNER_BYTES = Path(__file__).read_bytes()
CODE = ".claude/skills/codetrial-review/"
HASH = re.compile(r"[0-9a-f]{64}")
APPROVAL = re.compile(r"\s*(?:lgtm[.!]?|looks good[.!]?|approved[.!]?)\s*", re.I)


class Invalid(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*args):
    return subprocess.check_output(
        ["git", *args],
        cwd=ROOT,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        stderr=subprocess.DEVNULL,
        timeout=30,
    )


def read_json(path, limit=1024 * 1024, expected_hash=None):
    with open(path, "rb") as stream:
        value = stream.read(limit + 1)
    require(len(value) <= limit, "JSON input exceeds size limit")
    require(
        expected_hash is None or hashlib.sha256(value).hexdigest() == expected_hash,
        "query-set digest differs",
    )

    def unique(pairs):
        result = {}
        for key, item in pairs:
            require(key not in result, "duplicate JSON key")
            result[key] = item
        return result

    def finite(value):
        raise Invalid("nonfinite JSON value")

    return json.loads(value, object_pairs_hook=unique, parse_constant=finite)


@contextlib.contextmanager
def control(commit):
    require(
        bool(re.fullmatch(r"[0-9a-f]{40}", commit)), "use a full local control commit"
    )
    with tempfile.TemporaryDirectory(prefix="review-retrieval-control-") as directory:
        directory = Path(directory)
        for name in ("memory.py", "benchmark-memory.py"):
            (directory / name).write_bytes(git("show", commit + ":" + CODE + name))
        spec = importlib.util.spec_from_file_location(
            "retrieval_control", directory / "benchmark-memory.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            yield module
        finally:
            module.memory.METER.stop()


def copy_bounded(source, target, size):
    written = 0
    with open(source, "rb") as inp, open(target, "xb") as out:
        for block in iter(lambda: inp.read(64 * 1024), b""):
            written += len(block)
            require(written <= size, "snapshot input exceeds declared size")
            out.write(block)
    require(written == size, "snapshot input differs from declared size")


def verify_cache(path, benchmark):
    path = Path(path)
    record = read_json(path / "record.json", 64 * 1024)
    require(record.get("schema_version") == 2, "unsupported evaluation cache format")
    for suffix in ("-wal", "-shm", "-journal"):
        require(
            not Path(str(path / "memory.db") + suffix).exists(),
            "cache has journal sidecars",
        )
    for name, field in (
        ("memory.db", "database_sha256"),
        ("memory.db.gz", "gzip_sha256"),
        ("manifest.json", "manifest_sha256"),
    ):
        require(sha256(path / name) == record[field], "evaluation cache bytes changed")
    manifest = benchmark.memory.read_manifest(
        path / "manifest.json", benchmark.memory.REPO
    )
    require(
        hashlib.sha256(benchmark.COLLECTOR_BYTES).hexdigest()
        == record["identity"]["collector_sha256"],
        "control collector differs from frozen corpus",
    )
    require(
        hashlib.sha256(benchmark.RUNNER_BYTES).hexdigest() == record["helper_sha256"],
        "control digest helper differs from frozen cache",
    )
    require(
        manifest["generator_commit"] == record["identity"]["producer_commit"],
        "cache producer differs from manifest",
    )
    with contextlib.closing(
        benchmark.memory.validate_snapshot(
            str(path / "memory.db"), manifest, benchmark.memory.REPO
        )
    ) as db:
        require(
            benchmark.public_digest(db) == record["identity"]["public_corpus_sha256"],
            "cache corpus digest differs",
        )
        require(
            benchmark.derived_digest(db) == record["identity"]["public_index_sha256"],
            "cache index digest differs",
        )
    return record


def freeze(snapshot, commit):
    common = Path(git("rev-parse", "--git-common-dir").decode().strip())
    if not common.is_absolute():
        common = ROOT / common
    base = common / "codetrial-review/evaluation"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    with control(commit) as benchmark, (base / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with tempfile.TemporaryDirectory(prefix=".freeze-", dir=base) as temporary:
            stage = Path(temporary)
            manifest_path = Path(snapshot) / "manifest.json"
            require(
                manifest_path.stat().st_size <= 64 * 1024, "snapshot manifest too large"
            )
            copy_bounded(
                manifest_path, stage / "manifest.json", manifest_path.stat().st_size
            )
            manifest = benchmark.memory.read_manifest(
                stage / "manifest.json", benchmark.memory.REPO
            )
            require(
                manifest["index_version"] == benchmark.memory.VERSION,
                "control index version differs",
            )
            require(
                git("show", manifest["generator_commit"] + ":" + CODE + "memory.py")
                == benchmark.COLLECTOR_BYTES,
                "snapshot producer collector differs from control",
            )
            zipped = stage / "memory.db.gz"
            copy_bounded(
                Path(snapshot) / "memory.db.gz", zipped, manifest["compressed_bytes"]
            )
            require(sha256(zipped) == manifest["sha256"], "snapshot digest differs")
            size = 0
            with (
                gzip.open(zipped, "rb") as inp,
                (stage / "memory.db").open("xb") as out,
            ):
                for block in iter(lambda: inp.read(64 * 1024), b""):
                    size += len(block)
                    require(
                        size <= manifest["database_bytes"],
                        "snapshot expansion exceeds limit",
                    )
                    out.write(block)
            require(
                size == manifest["database_bytes"], "snapshot expanded size differs"
            )
            with contextlib.closing(
                benchmark.memory.validate_snapshot(
                    str(stage / "memory.db"), manifest, benchmark.memory.REPO
                )
            ) as db:
                identity = {
                    "producer_commit": manifest["generator_commit"],
                    "collector_sha256": hashlib.sha256(
                        benchmark.COLLECTOR_BYTES
                    ).hexdigest(),
                    "public_corpus_sha256": benchmark.public_digest(db),
                    "public_index_sha256": benchmark.derived_digest(db),
                    "manifest_sha256": sha256(stage / "manifest.json"),
                    "source_format": manifest["source_format"],
                    "index_version": manifest["index_version"],
                }
            record = {
                "schema_version": 2,
                "purpose": "private local evaluation cache; not GitHub-attested",
                "identity": identity,
                "helper_sha256": hashlib.sha256(benchmark.RUNNER_BYTES).hexdigest(),
                "database_sha256": sha256(stage / "memory.db"),
                "gzip_sha256": sha256(zipped),
                "manifest_sha256": identity["manifest_sha256"],
            }
            key = hashlib.sha256(
                json.dumps(record, sort_keys=True).encode()
            ).hexdigest()
            destination = base / identity["public_corpus_sha256"] / key
            destination.parent.mkdir(mode=0o700, exist_ok=True)
            (stage / "record.json").write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n"
            )
            if destination.exists():
                require(
                    read_json(destination / "record.json") == record,
                    "cache identity collision",
                )
                verify_cache(destination, benchmark)
            else:
                for item in stage.iterdir():
                    item.chmod(0o400)
                os.rename(stage, destination)
            return {"cache": str(destination), "identity": identity}


class StrictEmpty(Exception):
    pass


class ObservedCursor:
    def __init__(self, cursor, event):
        self.cursor, self.event = cursor, event

    def fetchall(self):
        rows = self.cursor.fetchall()
        self.event["materialized_candidates"] = len(rows)
        return rows

    def fetchone(self):
        row = self.cursor.fetchone()
        self.event["fetchone_rows"] = int(row is not None)
        return row


class ObservedDatabase:
    def __init__(self, db):
        self.db, self.events = db, []

    def execute(self, sql, params):
        event = {"fts": "docs_fts MATCH" in sql}
        self.events.append(event)
        return ObservedCursor(self.db.execute(sql, params), event)


class SourceScope:
    def __init__(self, db, key):
        self.db, self.key = db, key

    def execute(self, sql, params):
        # Run the pinned collector's filter-only path against exactly one gold
        # document, so its limits cannot conceal whether a label is in scope.
        if sql in ("SAVEPOINT review_memory_search", "RELEASE review_memory_search"):
            return self.db.execute(sql, params)
        require(
            "WHERE " in sql and "docs_fts MATCH" not in sql, "unexpected scope query"
        )
        return self.db.execute(
            sql.replace("WHERE ", "WHERE d.key = ? AND ", 1), [self.key] + params
        )


def validate_queries(data, identity, memory, db):
    require(
        type(data.get("schema_version")) is int and data["schema_version"] == 1,
        "unsupported query set format",
    )
    require(data.get("identity") == identity, "query corpus identity differs")
    require(
        isinstance(data.get("judgement_provenance"), str)
        and data["judgement_provenance"].strip(),
        "declared judgement provenance required",
    )
    queries = data.get("queries")
    require(
        isinstance(queries, list) and 1 <= len(queries) <= 1000, "invalid query count"
    )
    parents = {}

    def component(value):
        parents.setdefault(value, value)
        trail = []
        while parents[value] != value:
            trail.append(value)
            value = parents[value]
        for item in trail:
            parents[item] = value
        return value

    def join(left, right):
        left, right = component(left), component(right)
        parents[max(left, right)] = min(left, right)

    for left, right in db.execute("SELECT src,dst FROM thread_closes"):
        join(left, right)
    for query in queries:
        gold = query.get("gold", [])
        for item in gold[1:]:
            join(gold[0]["thread"], item["thread"])
    ids, groups = set(), {}
    for query in queries:
        name = query.get("id")
        require(
            isinstance(name, str)
            and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name)
            and name not in ids,
            "invalid or repeated query ID",
        )
        ids.add(name)
        require(query.get("split") in ("development", "holdout"), "invalid query split")
        require(
            query.get("slice") in ("rationale", "failure", "precedent", "inflight"),
            "invalid content slice",
        )
        require(
            isinstance(query.get("leakage"), dict)
            and type(query["leakage"].get("source_conditioned")) is bool,
            "declare source conditioning",
        )
        require(
            query.get("review_target_thread") is None,
            "only standalone lookup is supported",
        )
        require(
            isinstance(query.get("text"), str) and 0 < len(query["text"]) <= 4096,
            "invalid query text",
        )
        group = query.get("relationship_group")
        require(type(group) is int and group > 0, "invalid relationship group")
        require(
            groups.setdefault(group, query["split"]) == query["split"],
            "relationship group crosses holdout",
        )
        filters = query.get("filters", {})
        require(
            set(filters)
            <= {
                "files",
                "symbols",
                "errors",
                "trust_in",
                "kinds",
                "state",
                "since",
                "exclude",
                "limit",
                "per_thread",
            },
            "unsupported query filter",
        )
        require(
            filters.get("trust_in")
            and set(filters["trust_in"]) <= {"maintainer", "reported"},
            "explicit human-account trust scope required",
        )
        require(
            isinstance(filters.get("kinds"), list)
            and set(filters["kinds"]) <= set(memory.KINDS),
            "invalid source kind",
        )
        require(
            filters.get("state") in (None, "open", "closed", "merged"), "invalid state"
        )
        for field, maximum in (("limit", 100), ("per_thread", 10)):
            require(
                type(filters.get(field)) is int and 1 <= filters[field] <= maximum,
                "invalid query limit",
            )
        for field in ("files", "symbols", "errors", "kinds", "trust_in"):
            require(
                isinstance(filters.get(field, []), list)
                and all(isinstance(v, str) for v in filters.get(field, [])),
                "invalid query filter values",
            )
        require(
            isinstance(filters.get("exclude", []), list)
            and all(type(v) is int and v > 0 for v in filters.get("exclude", [])),
            "invalid exclusion",
        )
        require(
            filters.get("since") is None or isinstance(filters["since"], str),
            "invalid since filter",
        )
        require(
            isinstance(query.get("gold"), list) and query["gold"],
            "known positives required",
        )
        for gold in query["gold"]:
            require(group == component(gold["thread"]), "incorrect relationship group")
            row = db.execute(
                "SELECT d.*,t.deleted FROM documents d JOIN threads t ON t.number=d.thread WHERE d.key=?",
                (gold["key"],),
            ).fetchone()
            require(
                row and not row["projected"] and not row["deleted"],
                "gold source missing or incomplete",
            )
            require(
                row["thread"] == gold["thread"]
                and row["thread"] not in filters.get("exclude", []),
                "gold thread conflicts with query",
            )
            require(
                row["trust"] in filters["trust_in"], "gold trust outside query scope"
            )
            require(
                hashlib.sha256(row["body"].encode()).hexdigest() == gold["body_sha256"],
                "gold source text changed",
            )
            require(
                memory.search(SourceScope(db, gold["key"]), text="", **filters),
                "gold source outside explicit filters",
            )
    return queries


def metrics(rows, gold, limit=10):
    keys = [row["key"] for row in rows]
    threads = list(dict.fromkeys(row["thread"] for row in rows))
    documents = {row["key"] for row in gold}
    targets = {row["thread"] for row in gold}
    result = {"keys": keys, "threads": threads, "empty": not rows}
    for label, ranking, known in (
        ("document", keys, documents),
        ("thread", threads, targets),
    ):
        hits = [i for i, value in enumerate(ranking, 1) if value in known]
        result[label + "_reciprocal_rank"] = 1 / min(hits) if hits else 0
        result[label + "_known_positive_recall"] = {
            str(k): len(set(ranking[:k]) & known) / len(known) if k <= limit else None
            for k in (1, 3, 5, 10)
        }
    result["unjudged_keys"] = [key for key in keys if key not in documents]
    return result


def leakage_annotations(query, db, memory):
    words = re.findall(r"[a-z0-9_]+", query["text"].lower())
    triples = {tuple(words[i : i + 3]) for i in range(len(words) - 2)}
    matches = []
    for gold in query["gold"]:
        row = db.execute(
            "SELECT body,path FROM documents WHERE key=?", (gold["key"],)
        ).fetchone()
        source = re.findall(r"[a-z0-9_]+", row["body"].lower())
        shared = triples & {tuple(source[i : i + 3]) for i in range(len(source) - 2)}
        matches.append(
            {
                "key": gold["key"],
                "shared_query_trigrams": [" ".join(item) for item in sorted(shared)],
                "exact_named_identifiers": sorted(
                    name
                    for name in memory.named_in(row["body"])
                    if name.lower() in set(words)
                ),
                "source_path_used_as_filter": row["path"]
                in query["filters"].get("files", []),
                "generated_marker_in_source": bool(
                    re.search(r"auto-generated|summary by cubic", row["body"], re.I)
                ),
            }
        )
    return matches


def rendered_stdout(memory, rows, report, query, mode):
    """Use the pinned presentation without rerunning selection outside timing."""
    filters = query["filters"]
    args = SimpleNamespace(
        text=[query["text"]],
        file=filters.get("files", []),
        symbol=filters.get("symbols", []),
        error=filters.get("errors", []),
        trust=filters["trust_in"],
        kind=filters["kinds"],
        state=filters.get("state"),
        since=filters.get("since"),
        exclude=filters.get("exclude", []),
        limit=filters["limit"],
        strict=mode == "strict-body",
        candidate_limit=256,
        json=False,
        ref=None,
    )
    previous_search = memory.search
    previous_report = getattr(memory, "search_report", None)
    memory.search = lambda *args, **kwargs: rows
    if report is not None:
        memory.search_report = lambda *args, **kwargs: dict(report, rows=rows)
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            memory.cmd_search(None, args)
    finally:
        memory.search = previous_search
        if previous_report is not None:
            memory.search_report = previous_report
    return output.getvalue()


def run(cache, queries_path, query_hash, commit, mode, split):
    require(bool(HASH.fullmatch(query_hash)), "full query-set digest required")
    data = read_json(queries_path, expected_hash=query_hash)
    with control(commit) as benchmark:
        record = verify_cache(cache, benchmark)
        path = Path(cache) / "memory.db"
        before = sha256(path)
        memory = benchmark.memory
        with contextlib.closing(benchmark.readonly(path)) as db:
            sqlite_source_id = db.execute("SELECT sqlite_source_id()").fetchone()[0]
            queries = validate_queries(data, record["identity"], memory, db)
            results = []
            for query in queries:
                if split != "all" and query["split"] != split:
                    continue
                if mode == "strict-body" and query["filters"]["kinds"] != ["body"]:
                    continue
                runs = []
                for repeat in range(2):
                    observed, calls = ObservedDatabase(db), []
                    original = memory.fts_query

                    def instrument(text, operator="AND"):
                        if mode == "strict-body" and operator == "OR":
                            raise StrictEmpty()
                        value = original(text, operator)
                        if mode == "strict-body" and value:
                            value = "{title body}: (" + value + ")"
                        calls.append({"operator": operator, "effective": value})
                        return value

                    memory.fts_query = instrument
                    started = time.perf_counter()
                    report = None
                    try:
                        try:
                            if hasattr(memory, "search_report"):
                                report = memory.search_report(
                                    observed,
                                    text=query["text"],
                                    strict=mode == "strict-body",
                                    **query["filters"],
                                )
                                rows = report.pop("rows")
                            else:
                                rows = memory.search(
                                    observed, text=query["text"], **query["filters"]
                                )
                        except StrictEmpty:
                            rows = []
                    finally:
                        memory.fts_query = original
                    elapsed = time.perf_counter() - started
                    rendered = rendered_stdout(memory, rows, report, query, mode)
                    value = metrics(rows, query["gold"], query["filters"]["limit"])
                    value.update(
                        repeat=repeat + 1,
                        elapsed_s=elapsed,
                        evidence_bytes_utf8=len(rendered.encode()),
                        harness_fts_calls=calls,
                        harness_sql=observed.events,
                        retrieval=report,
                        unique_threads=len(value["threads"]),
                        mechanical_noise_paths=sum(
                            bool(memory.NOISE.search(row["path"] or "")) for row in rows
                        ),
                        short_approval_documents=sum(
                            bool(APPROVAL.fullmatch(row["body"])) for row in rows
                        ),
                        bot_authors=sum(
                            bool(memory.BOT.search(row["author"] or "")) for row in rows
                        ),
                    )
                    runs.append(value)
                results.append(
                    {
                        "id": query["id"],
                        "slice": query["slice"],
                        "split": query["split"],
                        "effective_filters": query["filters"],
                        "leakage": query["leakage"],
                        "mechanical_leakage": leakage_annotations(query, db, memory),
                        "stable_order": runs[0]["keys"] == runs[1]["keys"],
                        "runs": runs,
                    }
                )
            require(results, "no queries in requested split and mode")
        require(sha256(path) == before, "evaluation database changed during run")
        return {
            "schema_version": 1,
            "identity": record["identity"],
            "control_commit": commit,
            "entrypoint": "memory.search_report"
            if hasattr(memory, "search_report")
            else "memory.search",
            "collector_sha256": hashlib.sha256(benchmark.COLLECTOR_BYTES).hexdigest(),
            "helper_sha256": hashlib.sha256(benchmark.RUNNER_BYTES).hexdigest(),
            "harness_sha256": hashlib.sha256(RUNNER_BYTES).hexdigest(),
            "harness_changed_since_start": Path(__file__).read_bytes() != RUNNER_BYTES,
            "query_set_sha256": query_hash,
            "mode": mode,
            "split": split,
            "sqlite_version": sqlite3.sqlite_version,
            "sqlite_source_id": sqlite_source_id,
            "database_unchanged": True,
            "judgement_provenance": data.get("judgement_provenance"),
            "scope": "standalone historical lookup; known positives only; unjudged is not false positive",
            "thread_metric_scope": "deduplicated returned document window; not an independent k-thread search",
            "judgement_scope": "declared by frozen input; not independently verified human authorship",
            "rendering_scope": "pinned cmd_search presentation of measured rows with ref=None; excludes progress stderr",
            "results": results,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    cache = commands.add_parser("cache")
    cache.add_argument("--snapshot", required=True)
    cache.add_argument("--control-commit", required=True)
    measure = commands.add_parser("run")
    measure.add_argument("--cache", required=True)
    measure.add_argument("--queries", required=True)
    measure.add_argument("--queries-sha256", required=True)
    measure.add_argument("--control-commit", required=True)
    measure.add_argument(
        "--mode", choices=("default", "strict-body"), default="default"
    )
    measure.add_argument(
        "--split", choices=("development", "holdout", "all"), default="development"
    )
    measure.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "cache":
            print(json.dumps(freeze(args.snapshot, args.control_commit), indent=2))
        else:
            require(not Path(args.output).exists(), "output already exists")
            result = run(
                args.cache,
                args.queries,
                args.queries_sha256,
                args.control_commit,
                args.mode,
                args.split,
            )
            descriptor = os.open(
                args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "w") as stream:
                json.dump(result, stream, indent=2)
                stream.write("\n")
        return 0
    except Exception as error:
        # Inputs may contain private source data; exception messages stay local.
        print("retrieval evaluation failed: " + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
