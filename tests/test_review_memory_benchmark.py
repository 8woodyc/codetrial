#!/usr/bin/env python3
"""The offline benchmark must fail closed and never mutate its source."""

import contextlib
import importlib.util
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "memory_fixture", Path(__file__).with_name("test_review_memory.py")
)
fixtures = importlib.util.module_from_spec(FIXTURE_SPEC)
FIXTURE_SPEC.loader.exec_module(fixtures)
BENCHMARK = fixtures.MEMORY.with_name("benchmark-memory.py")


class BenchmarkTests(fixtures.MemoryCase):
    def setUp(self):
        super().setUp()
        spec = importlib.util.spec_from_file_location("memory_benchmark", BENCHMARK)
        self.benchmark = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.benchmark)
        self.addCleanup(self.benchmark.memory.METER.stop)
        history = json.loads(json.dumps(fixtures.HISTORY))
        history[fixtures.REPO + "/collaborators"] = [{"login": "maint"}]
        history[fixtures.REPO + "/pulls/2/files"][0]["patch"] += (
            "\n+fn alpha_one() {}\n+fn beta_two() {}\n+fn gamma_three() {}"
        )
        history[fixtures.REPO + "/pulls/comments"][0]["body"] += " ghp_" + "B" * 36
        history[fixtures.REPO + "/pulls/comments"][0]["updated_at"] = (
            self.benchmark.memory.now()
        )
        self.answer(history)
        self.run_memory("backfill")
        self.run_memory("sweep")
        self.source = self.dir / "memory.db"
        self.snapshot = self.dir / "snapshot"
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=BENCHMARK.parents[3], text=True
        ).strip()
        self.run_memory(
            "snapshot",
            "--output",
            str(self.snapshot),
            "--release-id",
            "review-memory-20261025T000000Z-1",
            "--generator-commit",
            commit,
        )
        self.manifest = json.loads((self.snapshot / "manifest.json").read_text())
        self.fixture = self.dir / "fixture.db"
        with contextlib.closing(self.benchmark.readonly(self.source)) as source:
            self.benchmark.create_fixture(source, self.fixture, self.manifest)

    def fixture_process(self, producer=None):
        def git_or_process(arguments, **kwargs):
            if arguments[:2] == ["git", "show"]:
                code = fixtures.MEMORY.read_bytes() if producer is None else producer
                return subprocess.CompletedProcess(arguments, 0, stdout=code)
            return subprocess.run(arguments, **kwargs)

        return SimpleNamespace(
            run=git_or_process,
            check_output=subprocess.check_output,
            SubprocessError=subprocess.SubprocessError,
            TimeoutExpired=subprocess.TimeoutExpired,
        )

    def arguments(self):
        return SimpleNamespace(
            source_db=str(self.source),
            snapshot=str(self.snapshot),
            trials=1,
            timeout=60,
        )

    def test_backfill_and_local_unattested_install_have_the_same_public_corpus(self):
        before = self.benchmark.sha256(self.source)
        # Producer bytes are a unit fixture, independent of uncommitted edits.
        arguments = self.arguments()
        arguments.trials = 2
        with patch.object(self.benchmark, "subprocess", self.fixture_process()):
            result = self.benchmark.run(arguments)
        backfill = result["trials"]["state_replay_backfill"][0]
        bootstrap = result["trials"]["local_unattested_ensure_zero_delta"][0]
        self.assertEqual(
            backfill["public_corpus_sha256"], bootstrap["public_corpus_sha256"]
        )
        self.assertEqual(
            backfill["public_index_sha256"], bootstrap["public_index_sha256"]
        )
        self.assertEqual(bootstrap["network_bytes"], 0)
        self.assertEqual(bootstrap["attestation_checks_stubbed"], 2)
        self.assertEqual(bootstrap["source_records_changed"], 0)
        self.assertEqual(bootstrap["pr_detail_invocations_simulated"], 0)
        self.assertGreater(backfill["gh_process_s"], backfill["fixture_service_s"])
        self.assertGreater(
            backfill["gh_invocations_simulated"], bootstrap["gh_invocations_simulated"]
        )
        self.assertEqual(backfill["iteration"], 1)
        self.assertEqual(backfill["position"], 1)
        self.assertEqual(bootstrap["position"], 2)
        self.assertIn("git_head_after", result["versions"])
        self.assertIn("dirty_after", result["versions"])
        self.assertIn("workflow-token permissions", result["unavailable_evidence"])
        self.assertEqual(result["schema_version"], 2)
        for values in result["trials"].values():
            self.assertEqual(len(values), 2)
            self.assertEqual([row["iteration"] for row in values], [1, 2])
            self.assertEqual(
                [row["position"] for row in values],
                [values[0]["position"], 3 - values[0]["position"]],
            )
            self.assertTrue(
                all(
                    len(row["load_average_before"]) == 3
                    and len(row["load_average_after"]) == 3
                    for row in values
                )
            )
        self.assertIn("no speedup claim", result["comparison_scope"])
        self.assertIn("not a chronological HTTP capture", result["comparison_scope"])
        self.assertNotIn("B" * 36, json.dumps(result))
        self.assertEqual(before, self.benchmark.sha256(self.source))

    def test_fixture_miss_is_recorded_and_never_simulates_a_deletion(self):
        log = self.dir / "miss.jsonl"
        result = subprocess.run(
            [
                sys.executable,
                str(BENCHMARK),
                "_fixture-gh",
                str(self.fixture),
                str(log),
                "api",
                "repos/sysprog21/codetrial/unexpected",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 9)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("404", result.stderr)
        self.assertNotIn("not found", result.stderr.lower())
        self.assertTrue(json.loads(log.read_text())["miss"])

    def test_listing_since_is_inclusive_and_unknown_query_flags_fail(self):
        prefix = "repos/sysprog21/codetrial/issues?state=all&sort=updated&direction=asc&per_page=100&page=1"
        with contextlib.closing(self.benchmark.readonly(self.fixture)) as db:
            first = self.benchmark.fixture_response(db, ["api", prefix])[0]
            boundary = first[0]["updated_at"]
            since = self.benchmark.fixture_response(
                db, ["api", prefix + "&since=" + boundary]
            )[0]
            self.assertIn(first[0], since)
            for arguments in (
                [
                    "api",
                    "repos/sysprog21/codetrial/collaborators?per_page=100&permission=push",
                    "--paginate",
                ],
                ["api", "repos/sysprog21/codetrial/pulls/2#fragment"],
                [
                    "api",
                    "repos/sysprog21/codetrial/pulls/2/files?per%5Fpage=100",
                    "--paginate",
                ],
                [
                    "api",
                    "repos/sysprog21/codetrial/issues?per_page=100&state=all&sort=updated&direction=asc&page=1",
                ],
                ["api", prefix.replace("page=1", "page=01")],
                ["api", "repos/sysprog21/codetrial/pulls/2?"],
                ["api", "repos/sysprog21/codetrial/pulls/02"],
                ["api", prefix + "&since=2026-01-01T00%3A00%3A00Z"],
                ["api", prefix + "&since=2026-01-01T00+00+00Z"],
                ["api", prefix + "&extra=true"],
                ["api", prefix + "&page=2"],
                ["api", prefix, "--method", "DELETE"],
            ):
                with self.assertRaises(self.benchmark.ReplayMiss):
                    self.benchmark.fixture_response(db, arguments)

    def test_paginated_detail_exercises_real_gh_json_parsing(self):
        import sqlite3

        values = [{"filename": "src/file_{}.rs".format(i)} for i in range(150)]
        with sqlite3.connect(self.fixture) as db:
            db.execute(
                "UPDATE raw SET payload=? WHERE key='files:2'", (json.dumps(values),)
            )
        log = self.dir / "pagination.jsonl"
        with self.benchmark.fixture_environment(self.fixture, log):
            result = self.benchmark.memory.gh(
                "repos/sysprog21/codetrial/pulls/2/files?per_page=100", paginate=True
            )
        self.assertEqual(result, values)
        record = json.loads(log.read_text())
        self.assertEqual(record["pages_simulated"], 2)
        self.assertFalse(record["miss"])

    def test_spaced_interpreter_never_falls_through_to_another_gh(self):
        executable_dir = self.dir / "python space"
        executable_dir.mkdir()
        executable = executable_dir / "python3"
        executable.symlink_to(sys.executable)
        fallback = self.dir / "fallback"
        fallback.mkdir()
        marker = self.dir / "fallback-used"
        fixtures.support.write_executable(
            fallback / "gh",
            "#!/bin/sh\nprintf fallback > " + shlex.quote(str(marker)) + "\nexit 77\n",
        )
        script = """import importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('b',sys.argv[1]);b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
with b.fixture_environment(Path(sys.argv[2]),Path(sys.argv[3])):
    value=b.memory.gh('repos/sysprog21/codetrial/pulls/2/files?per_page=100',paginate=True)
print(json.dumps({'spaced_interpreter':' ' in sys.executable,'files':len(value)}))
"""
        log = self.dir / "spaced-requests.jsonl"
        result = subprocess.run(
            [
                str(executable),
                "-c",
                script,
                str(BENCHMARK),
                str(self.fixture),
                str(log),
            ],
            env=dict(os.environ, PATH=str(fallback) + ":" + os.environ["PATH"]),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout), {"spaced_interpreter": True, "files": 1}
        )
        self.assertFalse(marker.exists())
        self.assertFalse(json.loads(log.read_text())["miss"])

    def test_miss_swallowed_by_detail_still_fails_the_trial(self):
        import sqlite3

        with sqlite3.connect(self.fixture) as db:
            db.execute("DELETE FROM raw WHERE key='files:2'")
        log = self.dir / "incomplete.jsonl"
        result = subprocess.run(
            [
                sys.executable,
                str(BENCHMARK),
                "_trial",
                "backfill",
                str(self.fixture),
                str(self.snapshot),
                str(self.dir / "incomplete.db"),
                str(log),
                self.manifest["generated_at"],
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unmatched fixture argv", result.stderr)
        self.assertTrue(
            any(json.loads(line)["miss"] for line in log.read_text().splitlines())
        )
        self.assertIn("still stale", Path(str(log) + ".core").read_text())

    def test_original_projected_or_bootstrapped_sources_are_rejected(self):
        import sqlite3

        for sql in (
            "INSERT INTO public_projection VALUES ('issue:1',1)",
            "INSERT INTO sync_state VALUES ('bootstrap','fixture','2026-01-01T00:00:00Z')",
        ):
            with sqlite3.connect(self.source) as db:
                db.execute("DELETE FROM public_projection")
                db.execute("DELETE FROM sync_state WHERE pass='bootstrap'")
                db.execute(sql)
            with (
                patch.object(self.benchmark, "subprocess", self.fixture_process()),
                self.assertRaisesRegex(
                    self.benchmark.ReplayMiss, "original local collection"
                ),
            ):
                self.benchmark.run(self.arguments())

    def test_collector_bytes_and_source_corpus_must_match_the_snapshot(self):
        import sqlite3

        with (
            patch.object(
                self.benchmark,
                "subprocess",
                self.fixture_process(b"different collector"),
            ),
            self.assertRaisesRegex(self.benchmark.ReplayMiss, "collector differs"),
        ):
            self.benchmark.run(self.arguments())
        with sqlite3.connect(self.source) as db:
            row = db.execute("SELECT payload FROM raw WHERE key='issue:1'").fetchone()
            payload = json.loads(row[0])
            payload["body"] += " changed"
            db.execute(
                "UPDATE raw SET payload=? WHERE key='issue:1'", (json.dumps(payload),)
            )
        with (
            patch.object(self.benchmark, "subprocess", self.fixture_process()),
            self.assertRaisesRegex(self.benchmark.ReplayMiss, "same projected corpus"),
        ):
            self.benchmark.run(self.arguments())

    def test_timing_summary_reports_known_median_and_ranges(self):
        rows = [
            {"wall_s": 100, "gh_process_s": 50, "fixture_service_s": 10},
            {"wall_s": 1, "gh_process_s": 2, "fixture_service_s": 0.2},
            {"wall_s": 3, "gh_process_s": 4, "fixture_service_s": 0.3},
        ]
        self.assertEqual(
            self.benchmark.timing_summary(rows),
            {
                "wall_s": {"median": 3, "min": 1, "max": 100},
                "gh_process_s": {"median": 4, "min": 2, "max": 50},
                "fixture_service_s": {"median": 0.3, "min": 0.2, "max": 10},
            },
        )

    def test_child_exception_class_is_forwarded_without_its_source_message(self):
        script = """import importlib.util,sqlite3,sys
spec=importlib.util.spec_from_file_location('b',sys.argv[1]);b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
def failed(args):raise sqlite3.DatabaseError('private source value')
b.memory.main=failed
raise SystemExit(b.main(['_trial','backfill',*sys.argv[2:]]))
"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(BENCHMARK),
                str(self.fixture),
                str(self.snapshot),
                str(self.dir / "failed.db"),
                str(self.dir / "failed.jsonl"),
                self.manifest["generated_at"],
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        error = str(self.benchmark.trial_failure(result, "backfill"))
        self.assertIn("DatabaseError", error)
        self.assertNotIn("private source value", error)

    def test_malformed_metrics_cannot_be_reported_as_successful_samples(self):
        valid = {
            "public_corpus_sha256": "a" * 64,
            "public_index_sha256": "b" * 64,
            "wall_s": 1,
            "gh_process_s": 0.5,
            "fixture_service_s": 0.1,
        }
        self.assertTrue(self.benchmark.valid_trial(valid))
        for field in ("wall_s", "gh_process_s", "fixture_service_s"):
            for value in (
                None,
                "private source",
                True,
                -1,
                float("nan"),
                float("inf"),
                10**1000,
            ):
                with self.subTest(field=field, value=value):
                    self.assertFalse(
                        self.benchmark.valid_trial({**valid, field: value})
                    )
        for value in (None, [], {}, {**valid, "public_corpus_sha256": "private"}):
            self.assertFalse(self.benchmark.valid_trial(value))

    def test_child_timeout_and_bad_json_keep_safe_arm_context(self):
        base = self.fixture_process()
        for failure in ("timeout", "json"):

            def execute(arguments, **kwargs):
                if len(arguments) > 2 and arguments[2] == "_trial":
                    if failure == "timeout":
                        raise subprocess.TimeoutExpired(["private-scratch-path"], 60)
                    return subprocess.CompletedProcess(
                        arguments, 0, stdout="not json", stderr="private traceback"
                    )
                return base.run(arguments, **kwargs)

            process = SimpleNamespace(**dict(vars(base), run=execute))
            with (
                self.subTest(failure=failure),
                patch.object(self.benchmark, "subprocess", process),
                self.assertRaises(self.benchmark.ReplayMiss) as error,
            ):
                self.benchmark.run(self.arguments())
            self.assertIn("backfill trial", str(error.exception))
            self.assertNotIn("private", str(error.exception))
            self.assertIn(
                "timed out" if failure == "timeout" else "unparseable",
                str(error.exception),
            )

    def test_raw_and_index_mismatches_have_distinct_diagnostics(self):
        import gzip

        expected_path = self.dir / "expected.db"
        expected_path.write_bytes(
            gzip.decompress((self.snapshot / "memory.db.gz").read_bytes())
        )
        with contextlib.closing(self.benchmark.readonly(expected_path)) as db:
            expected = {
                "public_corpus_sha256": self.benchmark.public_digest(db),
                "public_index_sha256": self.benchmark.derived_digest(db),
                "wall_s": 1,
                "gh_process_s": 0.5,
                "fixture_service_s": 0.1,
            }
        base = self.fixture_process()
        for field in ("public_corpus_sha256", "public_index_sha256"):

            def execute(arguments, **kwargs):
                if len(arguments) > 2 and arguments[2] == "_trial":
                    return subprocess.CompletedProcess(
                        arguments,
                        0,
                        stdout=json.dumps({**expected, field: "0" * 64}),
                        stderr="",
                    )
                return base.run(arguments, **kwargs)

            process = SimpleNamespace(**dict(vars(base), run=execute))
            with (
                self.subTest(field=field),
                patch.object(self.benchmark, "subprocess", process),
                self.assertRaises(self.benchmark.ReplayMiss) as error,
            ):
                self.benchmark.run(self.arguments())
            self.assertIn(
                "raw/projection" if field == "public_corpus_sha256" else "derived/FTS",
                str(error.exception),
            )

    def test_trial_failure_reports_static_reason_without_forwarding_tracebacks(self):
        result = subprocess.CompletedProcess(
            [],
            1,
            stderr="private traceback must not escape\n"
            + json.dumps(
                {
                    "benchmark_error": "ReplayMiss",
                    "reason": "incomplete trial collection",
                }
            ),
        )
        error = str(self.benchmark.trial_failure(result, "backfill"))
        self.assertIn("backfill", error)
        self.assertIn("ReplayMiss: incomplete trial collection", error)
        self.assertNotIn("private traceback", error)
        result.stderr = "private traceback containing source data"
        self.assertNotIn(
            "source data", str(self.benchmark.trial_failure(result, "ensure"))
        )

    def test_producer_unavailable_fails_before_creating_scratch(self):
        process = SimpleNamespace(
            run=lambda *args, **kwargs: subprocess.CompletedProcess(
                args, 128, stdout=b""
            ),
            check_output=subprocess.check_output,
        )
        scratch = Mock()
        with (
            patch.object(self.benchmark, "subprocess", process),
            patch.object(
                self.benchmark, "tempfile", SimpleNamespace(TemporaryDirectory=scratch)
            ),
            self.assertRaisesRegex(
                self.benchmark.ReplayMiss, "producer commit is not locally available"
            ),
        ):
            self.benchmark.run(self.arguments())
        scratch.assert_not_called()

    def test_git_identity_ignores_inherited_repository_overrides(self):
        script = (
            "import importlib.util,subprocess;from pathlib import Path; spec=importlib.util.spec_from_file_location('bench',%r);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);print(subprocess.check_output(['git','rev-parse','--show-toplevel'],cwd=Path(m.__file__).resolve().parents[3],env=m.git_environment(),text=True).strip())"
            % str(BENCHMARK)
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=dict(os.environ, GIT_DIR="/nonexistent", GIT_WORK_TREE="/nonexistent"),
            cwd=self.dir,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()), BENCHMARK.parents[3])

    def test_journal_sidecars_are_refused_before_reading_live_source(self):
        Path(str(self.source) + "-wal").write_bytes(b"in use")
        with self.assertRaisesRegex(self.benchmark.ReplayMiss, "closed, immutable"):
            self.benchmark.run(
                SimpleNamespace(
                    source_db=str(self.source),
                    snapshot=str(self.snapshot),
                    trials=1,
                    timeout=60,
                )
            )
