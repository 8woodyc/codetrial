#!/usr/bin/env python3
"""Measure frozen-state backfill and explicitly unattested local installation.

This is an offline pipeline-consistency experiment, not an API emulator, a
network speed comparison or a production verification bypass. Private source
payloads remain inside a temporary directory. Only projected corpus digests and
aggregate measurements leave it.
"""

import argparse
import contextlib
import gzip
import hashlib
import importlib.util
import json
import math
import os
import platform
import resource
from pathlib import Path
import re
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.parse

SPEC = importlib.util.spec_from_file_location(
    "benchmark_memory_core", Path(__file__).with_name("memory.py")
)
COLLECTOR_BYTES = Path(__file__).with_name("memory.py").read_bytes()
RUNNER_BYTES = Path(__file__).read_bytes()
memory = importlib.util.module_from_spec(SPEC)
exec(compile(COLLECTOR_BYTES, SPEC.origin, "exec"), memory.__dict__)


class ReplayMiss(Exception):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA trusted_schema=OFF")
    return db


def git_environment(environment=None):
    environment = os.environ if environment is None else environment
    return {
        name: value
        for name, value in environment.items()
        if not name.startswith("GIT_")
    }


def git_state(root):
    return {
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, env=git_environment(), text=True
        ).strip(),
        "dirty": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=root,
                env=git_environment(),
            )
        ),
    }


def trial_failure(result, arm):
    reason = "unclassified child failure"
    for line in reversed(result.stderr.splitlines()):
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(item, dict)
            and isinstance(item.get("benchmark_error"), str)
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", item["benchmark_error"])
        ):
            reason = item["benchmark_error"]
            if reason == "ReplayMiss":
                reason += ": " + memory.safe_label(item.get("reason", ""))[:240]
            break
    return ReplayMiss(
        "{} trial failed (exit {}): {}".format(arm, result.returncode, reason)
    )


def observed_gh_version():
    try:
        result = subprocess.run(
            ["gh", "--version"],
            env=git_environment(),
            capture_output=True,
            text=True,
            timeout=5,
        )
        line = result.stdout.splitlines()[0] if result.stdout else ""
        if result.returncode == 0 and line.startswith("gh version "):
            return memory.safe_label(line)[:240]
    except (OSError, subprocess.SubprocessError):
        pass
    return "unavailable"


def public_digest(db):
    digest = hashlib.sha256()
    for row in db.execute("SELECT key,thread,kind,payload FROM raw ORDER BY key"):
        payload, fields = memory.project_payload(json.loads(row["payload"]))
        inherited = db.execute(
            "SELECT fields FROM public_projection WHERE key=?", (row["key"],)
        ).fetchone()
        value = [
            row["key"],
            row["thread"],
            row["kind"],
            payload,
            fields + (inherited[0] if inherited else 0),
        ]
        digest.update((json.dumps(value, sort_keys=True) + "\n").encode())
    return digest.hexdigest()


def derived_digest(db):
    digest = hashlib.sha256()
    # Source identities rather than insertion-order row IDs; raw-source body
    # hashes can contain hidden values, so they never enter the shared digest.
    for row in db.execute(
        "SELECT number,kind,title,state,author,trust,url,created,updated,merged,merge_sha,files_complete,deleted FROM threads ORDER BY number"
    ):
        digest.update((json.dumps(tuple(row), sort_keys=True) + "\n").encode())
    for row in db.execute(
        "SELECT key,thread,source,author,trust,url,created,updated,commit_sha,path,line,side,parent,diff_hunk,body,projected FROM documents ORDER BY key"
    ):
        digest.update((json.dumps(tuple(row), sort_keys=True) + "\n").encode())
    for table in (
        "thread_files",
        "thread_symbols",
        "thread_errors",
        "thread_closes",
        "thread_commits",
        "fixes_fts",
    ):
        rows = [tuple(row) for row in db.execute("SELECT * FROM " + table)]
        for row in sorted(rows, key=lambda value: json.dumps(value)):
            digest.update((table + "\0" + json.dumps(row) + "\n").encode())
    for row in db.execute(
        "SELECT d.key,f.title,f.body,f.terms,f.paths FROM docs_fts f JOIN documents d ON d.id=f.rowid ORDER BY d.key"
    ):
        digest.update((json.dumps(tuple(row)) + "\n").encode())
    return digest.hexdigest()


def projected_store(db, path, repo):
    clean = memory.connect(str(path), repo)
    memory.copy_public_raw(db, clean)
    clean.executemany(
        "INSERT INTO authority VALUES (?)", db.execute("SELECT login FROM authority")
    )
    memory.rebuild_all(clean)
    clean.commit()
    return clean


def create_fixture(source, path, manifest):
    db = sqlite3.connect(path)
    try:
        db.executescript("""
CREATE TABLE raw (key TEXT PRIMARY KEY, thread INTEGER, kind TEXT, updated TEXT, identity INTEGER, payload TEXT);
CREATE INDEX listing ON raw(kind,updated,identity,key);
CREATE INDEX detail ON raw(kind,thread);
CREATE TABLE authority (login TEXT PRIMARY KEY);
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
""")
        for row in source.execute("SELECT * FROM raw ORDER BY key"):
            payload = json.loads(row["payload"])
            updated = payload.get("updated_at", "") if isinstance(payload, dict) else ""
            db.execute(
                "INSERT INTO raw VALUES (?,?,?,?,?,?)",
                (
                    row["key"],
                    row["thread"],
                    row["kind"],
                    updated,
                    payload.get("id", payload.get("number", 0))
                    if isinstance(payload, dict)
                    else 0,
                    row["payload"],
                ),
            )
        db.executemany(
            "INSERT INTO authority VALUES (?)",
            source.execute("SELECT login FROM authority"),
        )
        db.execute(
            "INSERT INTO metadata VALUES ('manifest',?)", (json.dumps(manifest),)
        )
        db.commit()
    finally:
        db.close()
    os.chmod(path, 0o600)


def fixture_response(db, arguments):
    if (
        len(arguments) not in (2, 3)
        or arguments[0] != "api"
        or (len(arguments) == 3 and arguments[2] != "--paginate")
    ):
        raise ReplayMiss("unsupported fixture argv")
    manifest = json.loads(
        db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0]
    )
    repo = manifest["repository"]
    parsed = urllib.parse.urlsplit(arguments[1])
    prefix = "repos/" + repo + "/"
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or "#" in arguments[1]
        or "%" in parsed.query
        or not parsed.path.startswith(prefix)
    ):
        raise ReplayMiss("unsupported fixture repository")
    tail = parsed.path[len(prefix) :]
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    params = dict(pairs)
    canonical = parsed.path + ("?" + parsed.query if parsed.query else "")
    if canonical != arguments[1] or parsed.query != "&".join(
        key + "=" + value for key, value in pairs
    ):
        raise ReplayMiss("nonliteral fixture argv")
    if len(params) != len(pairs):
        raise ReplayMiss("duplicate fixture query parameter")
    paginate = len(arguments) == 3
    if tail == "releases" and parsed.query == "per_page=100" and paginate:
        return [
            [
                {
                    "tag_name": manifest["release_id"],
                    "draft": False,
                    "published_at": manifest["generated_at"],
                }
            ]
        ]
    if (
        tail == "collaborators"
        and parsed.query == "permission=push&per_page=100"
        and paginate
    ):
        values = [
            {"login": row[0]}
            for row in db.execute("SELECT login FROM authority ORDER BY login")
        ]
        return [values[i : i + 100] for i in range(0, len(values), 100)] or [[]]

    listing = {
        "issues": ("issue", 100),
        "issues/comments": ("issue_comment", 100),
        "pulls/comments": ("review_comment", 50),
    }
    if tail in listing:
        kind, size = listing[tail]
        required = {"sort": "updated", "direction": "asc", "per_page": str(size)}
        if tail == "issues":
            required["state"] = "all"
        if (
            paginate
            or any(params.get(k) != v for k, v in required.items())
            or set(params) - set(required) - {"page", "since"}
        ):
            raise ReplayMiss("unsupported listing query")
        try:
            page = int(params["page"])
        except (KeyError, ValueError):
            raise ReplayMiss("invalid listing page") from None
        if page < 1:
            raise ReplayMiss("invalid listing page")
        expected_keys = (
            (["state"] if tail == "issues" else [])
            + ["sort", "direction", "per_page", "page"]
            + (["since"] if "since" in params else [])
        )
        if [key for key, _ in pairs] != expected_keys or str(page) != params["page"]:
            raise ReplayMiss("unexpected listing argv ordering")
        since = params.get("since", "")
        if since:
            memory.timestamp(since)
        rows = db.execute(
            "SELECT payload FROM raw WHERE kind=? AND updated>=? ORDER BY updated,identity,key LIMIT ? OFFSET ?",
            (kind, since, size, (page - 1) * size),
        )
        return [[json.loads(row[0]) for row in rows]]
    match = re.fullmatch(r"pulls/([1-9][0-9]*)(?:/(reviews|files|commits))?", tail)
    if not match:
        raise ReplayMiss("unknown fixture endpoint")
    number, detail = int(match[1]), match[2]
    if detail is None:
        if params or paginate:
            raise ReplayMiss("unsupported pull query")
        row = db.execute(
            "SELECT payload FROM raw WHERE key=?", ("pull:" + str(number),)
        ).fetchone()
        if row is None:
            raise ReplayMiss("missing pull fixture")
        return [json.loads(row[0])]
    if parsed.query != "per_page=100" or not paginate:
        raise ReplayMiss("unsupported detail query")
    if detail == "reviews":
        values = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT payload FROM raw WHERE kind='review' AND thread=? ORDER BY key",
                (number,),
            )
        ]
    else:
        row = db.execute(
            "SELECT payload FROM raw WHERE key=?", (detail + ":" + str(number),)
        ).fetchone()
        if row is None:
            raise ReplayMiss("missing detail fixture")
        values = json.loads(row[0])
    return [values[i : i + 100] for i in range(0, len(values), 100)] or [[]]


def serve_fixture(path, log, arguments):
    started = time.perf_counter()
    try:
        with contextlib.closing(readonly(path)) as db:
            pages = fixture_response(db, arguments)
        encoded = "\n".join(json.dumps(page) for page in pages)
        record = {
            "argv": arguments,
            "pages_simulated": len(pages),
            "bytes_simulated": len(encoded.encode()),
            "service_s": time.perf_counter() - started,
            "miss": False,
        }
        code = 0
    except Exception:
        # A neutral error must never look like an upstream deletion or auth failure.
        encoded = ""
        record = {
            "argv": arguments,
            "pages_simulated": 0,
            "bytes_simulated": 0,
            "service_s": time.perf_counter() - started,
            "miss": True,
        }
        code = 9
    with open(log, "a") as out:
        out.write(json.dumps(record) + "\n")
    if code:
        print("unmatched state-replay request", file=sys.stderr)
    else:
        print(encoded)
    return code


@contextlib.contextmanager
def fixture_environment(fixture, log, process_times=None):
    original = memory.subprocess

    class FixtureProcess:
        def __getattr__(self, name):
            return getattr(subprocess, name)

        def run(self, command, **kwargs):
            if not command or command[0] != "gh":
                with open(log, "a") as out:
                    out.write(
                        json.dumps(
                            {
                                "argv": list(command),
                                "pages_simulated": 0,
                                "bytes_simulated": 0,
                                "service_s": 0,
                                "miss": True,
                            }
                        )
                        + "\n"
                    )
                raise ReplayMiss("unexpected subprocess in offline trial")
            # An absolute argv bypasses executable PATH/shebang fallback even
            # when the selected Python or the frozen runner contains spaces.
            invocation = [
                sys.executable,
                str(Path(__file__).resolve()),
                "_fixture-gh",
                str(fixture),
                str(log),
            ] + list(command[1:])
            started = time.perf_counter()
            try:
                return subprocess.run(invocation, **kwargs)
            finally:
                if process_times is not None:
                    process_times.append(time.perf_counter() - started)

    memory.subprocess = FixtureProcess()
    try:
        yield
    finally:
        memory.subprocess = original


def trial(operation, fixture, snapshot, target, log, frozen):
    memory.now = lambda: frozen
    verifications = []
    if operation == "ensure":
        memory.verification_capabilities = lambda: None
        memory.verify_artifact = lambda *args: verifications.append(args[0])
        manifest = memory.read_manifest(
            str(Path(snapshot) / "manifest.json"), memory.REPO
        )
        root = "https://github.com/{}/releases/download/{}/".format(
            memory.REPO, manifest["release_id"]
        )

        def open_local(url, timeout):
            if url not in (root + "manifest.json", root + "memory.db.gz"):
                raise ReplayMiss("unexpected local asset")
            return open(Path(snapshot) / url[len(root) :], "rb")

        memory.open_asset = open_local
    changed = []
    original_store = memory.store_raw

    def counted_store(*args, **kwargs):
        result = original_store(*args, **kwargs)
        if result:
            changed.append(True)
        return result

    memory.store_raw = counted_store
    process_times = []
    phase_times = {}
    for name in (
        "fetch_snapshot",
        "download_asset",
        "validate_snapshot",
        "install_snapshot",
        "cmd_backfill",
        "cmd_poll",
        "cmd_detail",
    ):
        original = getattr(memory, name)

        def measured(*args, _name=name, _original=original, **kwargs):
            started = time.perf_counter()
            try:
                return _original(*args, **kwargs)
            finally:
                phase_times[_name] = (
                    phase_times.get(_name, 0) + time.perf_counter() - started
                )

        setattr(memory, name, measured)
    started = time.perf_counter()
    with (
        fixture_environment(fixture, log, process_times),
        open(log + ".core", "w") as core_log,
        contextlib.redirect_stdout(core_log),
        contextlib.redirect_stderr(core_log),
    ):
        arguments = ["--db", target, operation]
        if operation == "ensure":
            arguments += ["--budget", "600", "--max", "30"]
        code = memory.main(arguments)
    elapsed = time.perf_counter() - started
    records = (
        [json.loads(line) for line in Path(log).read_text().splitlines()]
        if Path(log).exists()
        else []
    )
    misses = sum(row["miss"] for row in records)
    if misses:
        raise ReplayMiss("unmatched fixture argv ({} misses)".format(misses))
    if code:
        raise ReplayMiss("collector exited {}".format(code))
    with contextlib.closing(readonly(target)) as db:
        if (
            memory.stale_prs(db)
            or memory.mark(db, "walk")
            or memory.mark(db, "backfill_detail")
            or memory.mark(db, "detail_attempt") != "complete"
            or memory.mark(db, "authority_attempt") != "success"
            or memory.mark(db, "authority_source") != "push-list"
        ):
            raise ReplayMiss("incomplete trial collection")
        detail_calls = sum(
            bool(re.search(r"/pulls/[1-9][0-9]*(?:[/?]|$)", row["argv"][1]))
            for row in records
            if len(row["argv"]) > 1
        )
        if operation == "ensure":
            if changed or detail_calls:
                raise ReplayMiss(
                    "zero-delta ensure rewrote source or fetched PR detail"
                )
            raw_hash, index_hash = public_digest(db), derived_digest(db)
        else:
            with tempfile.TemporaryDirectory(dir=Path(target).parent) as stage:
                with contextlib.closing(
                    projected_store(db, Path(stage) / "public.db", memory.REPO)
                ) as public:
                    raw_hash, index_hash = public_digest(public), derived_digest(public)
        return {
            "wall_s": elapsed,
            "phase_inclusive_s": phase_times,
            "phase_scope": "nested inclusive timings; wall includes fixture setup and gh_process_s (Python fixture startup and service); local bytes only, attestation stubbed; digest gates outside timed region",
            "ensure_budget_s": 600 if operation == "ensure" else None,
            "detail_max": 30 if operation == "ensure" else None,
            "peak_rss_including_gates": resource.getrusage(
                resource.RUSAGE_SELF
            ).ru_maxrss,
            "peak_rss_unit": "bytes" if sys.platform == "darwin" else "KiB",
            "gh_process_s": sum(process_times),
            "fixture_service_s": sum(row["service_s"] for row in records),
            "source_records_changed": len(changed),
            "pr_detail_invocations_simulated": detail_calls,
            "gh_invocations_simulated": len(records),
            "response_pages_simulated": sum(row["pages_simulated"] for row in records),
            "response_bytes_simulated": sum(row["bytes_simulated"] for row in records),
            "attestation_checks_stubbed": len(verifications),
            "network_bytes": 0,
            "public_corpus_sha256": raw_hash,
            "public_index_sha256": index_hash,
            "journal_mode": db.execute("PRAGMA journal_mode").fetchone()[0],
            "synchronous": db.execute("PRAGMA synchronous").fetchone()[0],
        }


def timing_summary(values):
    return {
        metric: {
            "median": statistics.median(row[metric] for row in values),
            "min": min(row[metric] for row in values),
            "max": max(row[metric] for row in values),
        }
        for metric in ("wall_s", "gh_process_s", "fixture_service_s")
    }


def valid_trial(value):
    if not isinstance(value, dict):
        return False
    for field in ("public_corpus_sha256", "public_index_sha256"):
        if not isinstance(value.get(field), str) or not re.fullmatch(
            r"[0-9a-f]{64}", value[field]
        ):
            return False
    return all(
        type(value.get(field)) in (int, float)
        and 0 <= value[field] <= sys.float_info.max
        and math.isfinite(value[field])
        for field in ("wall_s", "gh_process_s", "fixture_service_s")
    )


def run(args):
    for suffix in ("-wal", "-journal", "-shm"):
        if Path(str(args.source_db) + suffix).exists():
            raise ReplayMiss(
                "use a closed, immutable source copy without journal sidecars"
            )
    source_hash = sha256(args.source_db)
    with open(Path(args.snapshot) / "manifest.json", "rb") as source:
        manifest_bytes = source.read(64 * 1024 + 1)
    if len(manifest_bytes) > 64 * 1024:
        raise ReplayMiss("snapshot manifest is too large")
    data = memory.validate_manifest(
        json.loads(
            manifest_bytes,
            object_pairs_hook=memory.unique_object,
            parse_constant=memory.reject_constant,
        ),
        memory.REPO,
    )
    if data["index_version"] != memory.VERSION:
        raise ReplayMiss("rebuild a matching-version benchmark snapshot first")
    collector = Path(__file__).with_name("memory.py")
    root = Path(__file__).resolve().parents[3]
    initial_git = git_state(root)
    producer = subprocess.run(
        [
            "git",
            "show",
            data["generator_commit"] + ":.claude/skills/codetrial-review/memory.py",
        ],
        cwd=root,
        env=git_environment(),
        capture_output=True,
    )
    if producer.returncode:
        raise ReplayMiss(
            "snapshot producer commit is not locally available in the script checkout"
        )
    if (
        hashlib.sha256(producer.stdout).digest()
        != hashlib.sha256(COLLECTOR_BYTES).digest()
    ):
        raise ReplayMiss(
            "collector differs from snapshot producer; rebuild a local snapshot at this collector commit"
        )
    with tempfile.TemporaryDirectory(prefix="review-memory-benchmark-") as temporary:
        scratch = Path(temporary)
        fixture = scratch / "fixture.db"
        runner = scratch / "runner"
        runner.mkdir()
        (runner / "memory.py").write_bytes(COLLECTOR_BYTES)
        (runner / "benchmark-memory.py").write_bytes(RUNNER_BYTES)
        path = scratch / "snapshot.db"
        frozen_snapshot = scratch / "snapshot"
        frozen_snapshot.mkdir()
        (frozen_snapshot / "manifest.json").write_bytes(manifest_bytes)
        zipped = frozen_snapshot / "memory.db.gz"
        size, compressed_hash = 0, hashlib.sha256()
        with (
            open(Path(args.snapshot) / "memory.db.gz", "rb") as inp,
            open(zipped, "wb") as out,
        ):
            while True:
                block = inp.read(64 * 1024)
                if not block:
                    break
                size += len(block)
                if size > data["compressed_bytes"]:
                    raise ReplayMiss("snapshot compressed bytes exceed manifest limit")
                compressed_hash.update(block)
                out.write(block)
        if (
            size != data["compressed_bytes"]
            or compressed_hash.hexdigest() != data["sha256"]
        ):
            raise ReplayMiss("snapshot compressed bytes differ from manifest")
        size = 0
        with gzip.open(zipped, "rb") as inp, open(path, "wb") as out:
            while True:
                block = inp.read(64 * 1024)
                if not block:
                    break
                size += len(block)
                if size > data["database_bytes"]:
                    raise ReplayMiss("snapshot decompression exceeds manifest limit")
                out.write(block)
        if size != data["database_bytes"]:
            raise ReplayMiss("snapshot decompressed size differs from manifest")
        with (
            contextlib.closing(
                memory.validate_snapshot(str(path), data, memory.REPO)
            ) as snapshot,
            contextlib.closing(readonly(args.source_db)) as source,
        ):
            expected = memory.connect(":memory:")
            try:
                if (
                    memory.schema_objects(source) != memory.schema_objects(expected)
                    or source.execute("PRAGMA user_version").fetchone()[0]
                    != memory.VERSION
                ):
                    raise ReplayMiss("source schema differs from current collector")
            finally:
                expected.close()
            if source.execute(
                "SELECT 1 FROM public_projection LIMIT 1"
            ).fetchone() or memory.mark(source, "bootstrap"):
                raise ReplayMiss(
                    "state replay requires original local collection, not projected imports"
                )
            if memory.mark(source, "authority_source") != "push-list":
                raise ReplayMiss(
                    "state replay currently requires an authoritative push-list source"
                )
            memory.publication_ready(source, data["generated_at"])
            if public_digest(source) != public_digest(snapshot):
                raise ReplayMiss(
                    "source and snapshot do not share the same projected corpus"
                )
            if [
                tuple(row)
                for row in source.execute("SELECT login FROM authority ORDER BY login")
            ] != [
                tuple(row)
                for row in snapshot.execute(
                    "SELECT login FROM authority ORDER BY login"
                )
            ]:
                raise ReplayMiss("source authority differs from snapshot")
            expected_raw, expected_index = (
                public_digest(snapshot),
                derived_digest(snapshot),
            )
            create_fixture(source, fixture, data)
        results = {
            "state_replay_backfill": [],
            "local_unattested_ensure_zero_delta": [],
        }
        for iteration in range(args.trials):
            arms = (
                ("backfill", "ensure") if iteration % 2 == 0 else ("ensure", "backfill")
            )
            for position, arm in enumerate(arms, 1):
                with tempfile.TemporaryDirectory(dir=scratch) as work:
                    work = Path(work)
                    log = work / "requests.jsonl"
                    load_before = list(os.getloadavg())
                    try:
                        result = subprocess.run(
                            [
                                sys.executable,
                                str(runner / "benchmark-memory.py"),
                                "_trial",
                                arm,
                                str(fixture),
                                str(frozen_snapshot),
                                str(work / "memory.db"),
                                str(log),
                                data["generated_at"],
                            ],
                            cwd=root,
                            capture_output=True,
                            text=True,
                            timeout=args.timeout,
                        )
                    except subprocess.TimeoutExpired:
                        raise ReplayMiss(
                            "{} trial {}.{} timed out after {:g}s".format(
                                arm, iteration + 1, position, args.timeout
                            )
                        ) from None
                    if result.returncode:
                        raise trial_failure(result, arm)
                    try:
                        value = json.loads(result.stdout)
                    except ValueError:
                        raise ReplayMiss(
                            arm + " trial produced unparseable output"
                        ) from None
                    if not valid_trial(value):
                        raise ReplayMiss(arm + " trial produced malformed output")
                    value.update(
                        iteration=iteration + 1,
                        position=position,
                        load_average_before=load_before,
                        load_average_after=list(os.getloadavg()),
                    )
                    if value["public_corpus_sha256"] != expected_raw:
                        raise ReplayMiss(
                            arm + " public raw/projection corpus differs from snapshot"
                        )
                    if value["public_index_sha256"] != expected_index:
                        raise ReplayMiss(
                            arm + " public derived/FTS index differs from snapshot"
                        )
                    name = (
                        "state_replay_backfill"
                        if arm == "backfill"
                        else "local_unattested_ensure_zero_delta"
                    )
                    results[name].append(value)
        if sha256(args.source_db) != source_hash or any(
            Path(str(args.source_db) + suffix).exists()
            for suffix in ("-wal", "-journal", "-shm")
        ):
            raise ReplayMiss("original source file changed during measurement")
        final_git = git_state(root)
        versions = {
            "python": sys.version.split()[0],
            "sqlite": sqlite3.sqlite_version,
            "collector_sha256": hashlib.sha256(COLLECTOR_BYTES).hexdigest(),
            "benchmark_sha256": hashlib.sha256(RUNNER_BYTES).hexdigest(),
            "private_frozen_runner": True,
            "code_files_changed_since_start": sha256(collector)
            != hashlib.sha256(COLLECTOR_BYTES).hexdigest()
            or sha256(__file__) != hashlib.sha256(RUNNER_BYTES).hexdigest(),
            "collector_commit": data["generator_commit"],
            "gh": observed_gh_version(),
            "gh_api_exercised": False,
            "gh_version_scope": "parent CLI version probe only; trial CLI replaced by Python state fixture",
            "git_head": initial_git["head"],
            "dirty": initial_git["dirty"],
            "git_head_after": final_git["head"],
            "dirty_after": final_git["dirty"],
            "source_format": memory.SOURCE_FORMAT,
            "index_version": memory.VERSION,
        }
        return {
            "schema_version": 2,
            "mode": "offline_state_fixture_with_unattested_local_install",
            "network_bandwidth_bytes_per_s": None,
            "signed_download_measurement": None,
            "unavailable_evidence": [
                "real GitHub API/network behavior",
                "signed download and cryptographic attestation latency",
                "workflow-token permissions",
                "controlled cold-cache behavior",
                "failure-rate estimates",
            ],
            "comparison_scope": "state fixture derived from frozen original local collection, not a chronological HTTP capture; offline pipeline consistency with fixture overhead; no network or attestation; no speedup claim",
            "tie_order": "updated_at then numeric source id (issue number fallback); deterministic fixture, GitHub tie behavior not validated",
            "cache_state": "uncontrolled warm OS cache; fresh database per arm, interleaved order",
            "versions": versions,
            "host": {
                "platform": platform.platform(),
                "cpu_count": os.cpu_count(),
                "load_average_after": list(os.getloadavg()),
                "scratch_block_bytes": os.statvfs(scratch).f_bsize,
            },
            "aborts_on_first_failure": True,
            "failure_scope": "successful samples only, not a failure-rate measurement",
            "fixture": {
                "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "database_gzip_sha256": data["sha256"],
                "release_id": data["release_id"],
                "frozen_source_clock": data["generated_at"],
                "passes": data["passes"],
                "public_corpus_sha256": expected_raw,
                "public_index_sha256": expected_index,
                "counts": data["counts"],
                "authority_source": data["authority_source"],
                "would_download_bytes": data["compressed_bytes"],
                "database_bytes": data["database_bytes"],
            },
            "trials": results,
            "summary": {
                name: timing_summary(values) for name, values in results.items()
            },
        }


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0] == "_fixture-gh":
        return serve_fixture(arguments[1], arguments[2], arguments[3:])
    if arguments and arguments[0] == "_trial":
        try:
            print(json.dumps(trial(*arguments[1:]), sort_keys=True))
            return 0
        except Exception as error:
            print(
                json.dumps(
                    {
                        "benchmark_error": type(error).__name__,
                        "reason": memory.safe_label(str(error))[:240]
                        if isinstance(error, ReplayMiss)
                        else None,
                    }
                ),
                file=sys.stderr,
            )
            return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args(arguments)
    if not 1 <= args.trials <= 10 or not 1 <= args.timeout <= 3600:
        parser.error("trials or timeout out of bounds")
    try:
        print(json.dumps(run(args), indent=2, sort_keys=True))
        return 0
    except (
        ReplayMiss,
        memory.Fail,
        sqlite3.DatabaseError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
    ) as error:
        print("benchmark failed: " + memory.safe_label(str(error)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
