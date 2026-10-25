#!/usr/bin/env python3
"""Bounded retrieval keeps explicit scopes and reports incomplete windows."""

import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

SPEC = importlib.util.spec_from_file_location(
    "retrieval_fixtures", Path(__file__).with_name("test_review_memory.py")
)
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)


class FetchRecorder:
    def __init__(self, db):
        self.db, self.fetched = db, []

    def execute(self, sql, params):
        cursor = self.db.execute(sql, params)
        owner = self

        class Cursor:
            def fetchone(self):
                return cursor.fetchone()

            def fetchall(self):
                rows = cursor.fetchall()
                owner.fetched.append(len(rows))
                return rows

        return Cursor()


class RetrievalTests(fixtures.MemoryCase):
    def setUp(self):
        super().setUp()
        self.run_memory("backfill")
        self.memory = self.memory_module()
        self.addCleanup(self.memory.METER.stop)
        self.db = sqlite3.connect(self.dir / "memory.db")
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)

    def test_fallback_is_disclosed_only_for_an_effectively_wider_query(self):
        for text in (
            "nonexistent_unique",
            "nonexistent_unique nonexistent_unique",
            "reconnect",
        ):
            report = self.memory.search_report(self.db, text)
            self.assertFalse(report["fallback"])
            self.assertEqual(report["mode"], "AND")
        wide = self.memory.search_report(self.db, "reconnect nonexistent_unique")
        self.assertTrue(wide["fallback"])
        self.assertEqual(wide["mode"], "OR")
        self.assertTrue(wide["rows"])
        strict = self.memory.search_report(
            self.db, "reconnect nonexistent_unique", strict=True
        )
        self.assertFalse(strict["fallback"])
        self.assertEqual(strict["rows"], [])
        self.assertEqual(strict["known_total_documents"], 0)

    def test_punctuation_and_unknown_text_do_not_turn_into_filter_only_search(self):
        for text in ("***", "x", '"'):
            report = self.memory.search_report(self.db, text, files=["src/livekit.rs"])
            self.assertEqual(report["rows"], [])
            self.assertEqual(report["mode"], "none")
            self.assertFalse(report["searched"])
            self.assertIsNone(report["known_total_documents"])
            self.assertEqual(report["empty_reason"], "no searchable terms")
        self.assertEqual(self.memory.search_report(self.db, "")["mode"], "filters")

    def test_explicit_empty_trust_and_other_filters_never_widen(self):
        report = self.memory.search_report(self.db, "reconnect", trust_in=[])
        self.assertEqual(report["rows"], [])
        self.assertEqual(report["effective_filters"]["trust_in"], [])
        filters = dict(
            files=["src/livekit.rs"],
            trust_in=["maintainer"],
            kinds=["review_comment"],
            state="merged",
            exclude=[3, 4],
        )
        report = self.memory.search_report(
            self.db, "RECONNECT_LIMIT nonexistent_unique", **filters
        )
        self.assertTrue(report["fallback"])
        self.assertTrue(report["rows"])
        for row in report["rows"]:
            self.assertEqual(row["thread"], 2)
            self.assertEqual(row["trust"], "maintainer")
            self.assertEqual(row["source"], "review_comment")
        self.assertEqual(report["effective_filters"]["exclude"], [3, 4])
        self.assertEqual(
            self.memory.search_report(self.db, "reconnect", files=["missing.rs"])[
                "rows"
            ],
            [],
        )

    def test_candidate_cap_bounds_python_materialization_and_exposes_omissions(self):
        # Populate source records in one transaction: collection's per-page
        # rebuild costs are separate from the retrieval window under test.
        for i in range(1000):
            number = 1 + i % 4
            self.memory.store_raw(
                self.db,
                "issue_comment:{}".format(1000 + i),
                number,
                "issue_comment",
                fixtures.comment(
                    1000 + i, number, "boundedcandidatefixture same content"
                ),
            )
        self.memory.rebuild_all(self.db)
        self.db.commit()
        observed = FetchRecorder(self.db)
        report = self.memory.search_report(
            observed,
            "boundedcandidatefixture",
            candidate_limit=20,
            limit=10,
            per_thread=2,
        )
        self.assertEqual(report["known_total_documents"], 1000)
        self.assertEqual(report["known_total_threads"], 4)
        self.assertEqual(report["materialized_candidates"], 20)
        self.assertEqual(report["outside_candidate_limit"], 980)
        self.assertTrue(report["candidate_limited"])
        self.assertTrue(report["eligible_beyond_candidates_unknown"])
        self.assertLessEqual(max(observed.fetched), 10)
        self.assertEqual(
            report["diversity_suppressed_within_candidates"],
            20 - report["eligible_within_candidates"],
        )
        per_thread = {}
        for row in report["rows"]:
            per_thread[row["thread"]] = per_thread.get(row["thread"], 0) + 1
        self.assertLessEqual(max(per_thread.values()), 2)
        full = self.memory.search_report(
            self.db,
            "boundedcandidatefixture",
            candidate_limit=1000,
            limit=10,
            per_thread=2,
        )
        self.assertEqual(len(full["rows"]), 8)
        self.assertFalse(full["candidate_limited"])
        self.assertEqual(full["eligible_within_candidates"], 8)

    def test_json_and_full_source_followups_round_trip(self):
        result = json.loads(
            self.run_memory(
                "search", "reconnect", "--json", "--candidate-limit", "4"
            ).stdout
        )
        self.assertEqual(result["retrieval"]["candidate_limit"], 4)
        self.assertIn("Untrusted historical evidence", result["notice"])
        self.assertTrue(result["results"])
        for row in result["results"]:
            self.assertEqual(row["followup"], "memory.py show " + row["key"])
            self.assertIn(row["key"], row["rendered"])
            shown = self.run_memory("show", row["key"]).stdout
            self.assertNotIn("no document", shown)
            self.assertIn(row["url"], shown)
        text = self.run_memory("search", "reconnect", "--strict").stdout
        self.assertIn("strict=true", text)
        self.assertIn("fallback=false", text)

    def test_ties_and_filter_only_order_are_stable(self):
        for text in ("reconnect", ""):
            first = self.memory.search_report(self.db, text, limit=4, per_thread=1)
            second = self.memory.search_report(self.db, text, limit=4, per_thread=1)
            self.assertEqual(
                [r["key"] for r in first["rows"]], [r["key"] for r in second["rows"]]
            )
            self.assertEqual(
                len({r["thread"] for r in first["rows"]}), len(first["rows"])
            )

    def test_invalid_limits_fail_instead_of_silently_returning_one_row(self):
        for args in (
            {"limit": 0},
            {"per_thread": 0},
            {"candidate_limit": 0},
            {"candidate_limit": 1001},
        ):
            with self.subTest(args=args), self.assertRaises(self.memory.Fail):
                self.memory.search_report(self.db, "reconnect", **args)

    def test_evaluation_observer_supports_bounded_search_count_queries(self):
        spec = importlib.util.spec_from_file_location(
            "bounded_evaluation", fixtures.MEMORY.with_name("evaluate-memory.py")
        )
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        observed = tool.ObservedDatabase(self.db)
        rows = self.memory.search(observed, "reconnect", limit=2)
        self.assertEqual(len(rows), 2)
        self.assertTrue(
            any(event.get("fetchone_rows") == 1 for event in observed.events)
        )
        self.assertTrue(
            any(event.get("materialized_candidates") == 2 for event in observed.events)
        )

    def test_counts_and_evidence_use_one_snapshot_during_concurrent_sync(self):
        self.db.execute("PRAGMA journal_mode=WAL").fetchone()
        victim = self.db.execute(
            "SELECT id,key FROM documents WHERE key='body:1'"
        ).fetchone()
        written, errors = [], []

        def sync_between_count_and_selection(sql):
            if not sql.startswith("WITH candidates") or written:
                return
            written.append(True)
            try:
                writer = sqlite3.connect(self.dir / "memory.db", timeout=1)
                try:
                    writer.execute(
                        "DELETE FROM docs_fts WHERE rowid=?", (victim["id"],)
                    )
                    writer.execute("DELETE FROM documents WHERE id=?", (victim["id"],))
                    writer.commit()
                finally:
                    writer.close()
            except Exception as error:
                errors.append(error)

        self.db.set_trace_callback(sync_between_count_and_selection)
        try:
            report = self.memory.search_report(self.db, "reconnect", kinds=["body"])
        finally:
            self.db.set_trace_callback(None)
        self.assertEqual(errors, [])
        self.assertTrue(written)
        self.assertIn(victim["key"], [row["key"] for row in report["rows"]])
        self.assertEqual(report["known_total_documents"], len(report["rows"]))
        after = self.memory.search_report(self.db, "reconnect", kinds=["body"])
        self.assertEqual(
            after["known_total_documents"], report["known_total_documents"] - 1
        )
        self.assertNotIn(victim["key"], [row["key"] for row in after["rows"]])

    def test_read_snapshot_does_not_commit_caller_writes_or_leak_after_failure(self):
        self.assertFalse(self.db.in_transaction)
        with self.assertRaises(self.memory.Fail):
            self.memory.search_report(self.db, "reconnect", limit=0)
        self.assertFalse(self.db.in_transaction)
        self.db.execute("UPDATE repository SET name=name")
        self.memory.search_report(self.db, "reconnect")
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()

    def test_scope_metadata_preserves_iterators_and_normalized_paths(self):
        report = self.memory.search_report(
            self.db,
            "",
            files=iter(["/src/livekit.rs/"]),
            exclude=iter([3, 4]),
            trust_in=["maintainer"],
        )
        self.assertEqual(report["effective_filters"]["files"], ["src/livekit.rs"])
        self.assertEqual(report["effective_filters"]["exclude"], [3, 4])
        self.assertTrue(report["rows"])
        self.assertTrue(all(row["thread"] == 2 for row in report["rows"]))

    def test_authority_refresh_failure_is_degraded_and_preserves_cached_roles(self):
        history = json.loads(json.dumps(fixtures.HISTORY))
        history[fixtures.REPO + "/collaborators"] = [{"login": "maint"}]
        self.answer(history)
        self.run_memory("backfill")
        history[fixtures.REPO + "/collaborators"] = {"fail": "HTTP 503 unavailable"}
        self.answer(history)
        result = self.run_memory("ensure", "--budget", "5", ok=False)
        value = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(value["outcome"], "degraded")
        self.assertEqual(value["code"], "authority-unavailable")
        self.assertEqual(
            value["status"]["passes"]["authority_attempt"]["mark"], "unavailable"
        )
        self.assertEqual(
            self.db.execute("SELECT login FROM authority").fetchone()[0], "maint"
        )

    def test_context_no_migrate_does_not_retry_timed_out_index_rebuild(self):
        self.db.execute("PRAGMA user_version = {}".format(self.memory.VERSION - 1))
        self.db.commit()
        bundle = self.dir / "bundle"
        bundle.mkdir()
        (bundle / "manifest.json").write_text('{"files":[],"closes":[]}')
        script = """import contextlib,importlib.util,io,json,sys
spec=importlib.util.spec_from_file_location('m',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
calls=[]
def slow(db):
    calls.append(m.DEADLINE is not None)
    raise m.BudgetExhausted("injected migration budget exhaustion")
def network(*args,**kwargs):raise AssertionError('migration must not access GitHub')
m.rebuild_all=slow;m.gh=network
first=io.StringIO()
with contextlib.redirect_stdout(first):rc=m.main(['--db',sys.argv[2],'ensure','--budget','5'])
second=io.StringIO()
with contextlib.redirect_stdout(second):context_rc=m.main(['--db',sys.argv[2],'context','--no-migrate',sys.argv[3],'--ref','HEAD'])
print(json.dumps({'ensure_exit':rc,'ensure':json.loads(first.getvalue()),'context_exit':context_rc,'rebuild_calls':calls}))
"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(fixtures.MEMORY),
                str(self.dir / "memory.db"),
                str(bundle),
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["ensure_exit"], 2)
        self.assertIsNone(value["ensure"]["status"])
        self.assertEqual(value["context_exit"], 1)
        self.assertEqual(value["rebuild_calls"], [True])
        self.assertIn("local index is not ready", result.stderr)
        self.assertEqual(
            self.db.execute("PRAGMA user_version").fetchone()[0],
            self.memory.VERSION - 1,
        )

    def test_no_migrate_reads_a_ready_store_and_does_not_create_missing_store(self):
        bundle = self.dir / "bundle"
        bundle.mkdir()
        (bundle / "manifest.json").write_text('{"files":[],"closes":[]}')
        result = self.run_memory(
            "context", "--no-migrate", str(bundle), "--ref", "HEAD"
        )
        self.assertEqual(result.returncode, 0)
        missing = self.dir / "missing" / "memory.db"
        with self.assertRaises(self.memory.Fail):
            self.memory.connect(str(missing), allow_migration=False)
        self.assertFalse(missing.parent.exists())
