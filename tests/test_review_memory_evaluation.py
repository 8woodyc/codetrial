#!/usr/bin/env python3
"""Frozen retrieval controls must preserve sources, scopes and holdouts."""

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "evaluation_fixtures", Path(__file__).with_name("test_review_memory.py")
)
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
TOOL = fixtures.MEMORY.with_name("evaluate-memory.py")


class EvaluationTests(fixtures.MemoryCase):
    def setUp(self):
        super().setUp()
        spec = importlib.util.spec_from_file_location("evaluation", TOOL)
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)
        history = json.loads(json.dumps(fixtures.HISTORY))
        history[fixtures.REPO + "/collaborators"] = [{"login": "maint"}]
        self.answer(history)
        self.run_memory("backfill")
        self.run_memory("sweep")
        self.commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=TOOL.parents[3], text=True
        ).strip()
        self.snapshot = self.dir / "snapshot"
        self.run_memory(
            "snapshot",
            "--output",
            str(self.snapshot),
            "--release-id",
            "review-memory-20261025T000000Z-1",
            "--generator-commit",
            self.commit,
        )
        original = self.tool.git
        self.common = self.dir / "git-common"
        self.common.mkdir()

        def git(*args):
            if args == ("rev-parse", "--git-common-dir"):
                return str(self.common).encode()
            return original(*args)

        patcher = patch.object(self.tool, "git", git)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cache = self.tool.freeze(self.snapshot, self.commit)
        self.cache_path = Path(self.cache["cache"])
        self.dataset = {
            "schema_version": 1,
            "identity": self.cache["identity"],
            "judgement_provenance": "model-authored source-conditioned known positives",
            "queries": [
                self.query("development", "body:1", 1),
                self.query("holdout", "body:4", 4),
            ],
        }
        self.queries = self.dir / "queries.json"
        self.save_queries()

    def query(self, split, key, group):
        with self.tool.control(self.commit) as control:
            with contextlib.closing(
                control.readonly(self.cache_path / "memory.db")
            ) as db:
                row = db.execute(
                    "SELECT * FROM documents WHERE key=?", (key,)
                ).fetchone()
        return {
            "id": split + "-query",
            "slice": "failure",
            "split": split,
            "review_target_thread": None,
            "relationship_group": group,
            "text": "reconnect",
            "leakage": {"source_conditioned": True},
            "filters": {
                "files": [],
                "trust_in": ["maintainer", "reported"],
                "kinds": ["body"],
                "state": None,
                "exclude": [],
                "limit": 10,
                "per_thread": 3,
            },
            "gold": [
                {
                    "key": key,
                    "thread": row["thread"],
                    "body_sha256": hashlib.sha256(row["body"].encode()).hexdigest(),
                }
            ],
        }

    def save_queries(self):
        self.queries.write_text(json.dumps(self.dataset))
        self.digest = self.tool.sha256(self.queries)

    def run_evaluation(self, mode="default", split="development"):
        return self.tool.run(
            self.cache_path, self.queries, self.digest, self.commit, mode, split
        )

    def test_cache_is_reusable_immutable_and_default_does_not_score_holdout(self):
        before = self.tool.sha256(self.cache_path / "memory.db")
        self.assertEqual(self.tool.freeze(self.snapshot, self.commit), self.cache)
        result = self.run_evaluation()
        self.assertEqual([q["id"] for q in result["results"]], ["development-query"])
        query = result["results"][0]
        self.assertTrue(query["stable_order"])
        self.assertIn("body:1", query["runs"][0]["keys"])
        self.assertEqual(query["runs"][0]["document_known_positive_recall"]["10"], 1)
        self.assertTrue(result["database_unchanged"])
        self.assertEqual(self.tool.sha256(self.cache_path / "memory.db"), before)
        self.assertEqual((self.cache_path / "memory.db").stat().st_mode & 0o777, 0o400)
        self.assertEqual(self.cache_path.stat().st_mode & 0o777, 0o700)

    def test_changed_query_bytes_cannot_reuse_a_frozen_digest(self):
        self.queries.write_text(self.queries.read_text() + "\n")
        with self.assertRaisesRegex(self.tool.Invalid, "query-set digest"):
            self.run_evaluation()

    def test_harness_edit_is_disclosed_without_relabeling_loaded_code(self):
        changed = self.dir / "changed-evaluator.py"
        changed.write_bytes(self.tool.RUNNER_BYTES + b"\n")
        with patch.object(self.tool, "__file__", str(changed)):
            result = self.run_evaluation()
        self.assertTrue(result["harness_changed_since_start"])
        self.assertEqual(
            result["harness_sha256"], hashlib.sha256(self.tool.RUNNER_BYTES).hexdigest()
        )

    def test_source_gold_must_match_hash_projection_and_explicit_scope(self):
        for mutation in ("hash", "path", "state", "exclude", "kind"):
            original = json.loads(json.dumps(self.dataset))
            query = self.dataset["queries"][0]
            if mutation == "hash":
                query["gold"][0]["body_sha256"] = "0" * 64
            elif mutation == "path":
                query["filters"]["files"] = ["never/existed.rs"]
            elif mutation == "state":
                query["filters"]["state"] = "open"
            elif mutation == "kind":
                query["filters"]["kinds"] = ["review_comment"]
            else:
                query["filters"]["exclude"] = [1]
            self.save_queries()
            with self.subTest(mutation=mutation), self.assertRaises(self.tool.Invalid):
                self.run_evaluation()
            self.dataset = original

    def test_actual_closing_component_cannot_be_split_by_relabeling_groups(self):
        holdout = self.query("holdout", "body:2", 2)
        self.dataset["queries"][1] = holdout
        self.save_queries()
        with self.assertRaisesRegex(self.tool.Invalid, "incorrect relationship group"):
            self.run_evaluation()
        holdout["relationship_group"] = 1
        self.save_queries()
        with self.assertRaisesRegex(self.tool.Invalid, "crosses holdout"):
            self.run_evaluation()

    def test_cache_tampering_and_journals_fail_before_scoring(self):
        sidecar = Path(str(self.cache_path / "memory.db") + "-wal")
        sidecar.write_bytes(b"unfinished")
        with self.assertRaisesRegex(self.tool.Invalid, "sidecars"):
            self.run_evaluation()
        sidecar.unlink()
        manifest = self.cache_path / "manifest.json"
        manifest.chmod(0o600)
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        with self.assertRaisesRegex(self.tool.Invalid, "bytes changed"):
            self.run_evaluation()

    def test_strict_body_control_does_not_fall_back_to_OR(self):
        self.dataset["queries"][0]["text"] = "reconnect nonexistentunique"
        self.save_queries()
        default = self.run_evaluation()["results"][0]["runs"][0]
        strict = self.run_evaluation("strict-body")["results"][0]["runs"][0]
        self.assertIn("body:1", default["keys"])
        self.assertEqual(len(default["harness_fts_calls"]), 2)
        self.assertEqual(strict["keys"], [])
        self.assertEqual(sum(event["fts"] for event in strict["harness_sql"]), 1)
        self.assertEqual(strict["harness_fts_calls"][0]["operator"], "AND")

    def test_projected_source_cannot_be_a_complete_gold_label(self):
        history = json.loads(json.dumps(fixtures.HISTORY))
        history[fixtures.REPO + "/collaborators"] = [{"login": "maint"}]
        history[fixtures.REPO + "/issues"][0]["body"] += " ghp_" + "B" * 36
        self.answer(history)
        self.run_memory("backfill")
        self.run_memory("sweep")
        snapshot = self.dir / "projected-snapshot"
        self.run_memory(
            "snapshot",
            "--output",
            str(snapshot),
            "--release-id",
            "review-memory-20261025T000000Z-2",
            "--generator-commit",
            self.commit,
        )
        self.cache = self.tool.freeze(snapshot, self.commit)
        self.cache_path = Path(self.cache["cache"])
        self.dataset["identity"] = self.cache["identity"]
        self.dataset["queries"][0] = self.query("development", "body:1", 1)
        self.save_queries()
        with self.assertRaisesRegex(self.tool.Invalid, "source missing or incomplete"):
            self.run_evaluation()

    def test_mechanical_leakage_is_separate_from_declared_judgement(self):
        query = self.dataset["queries"][0]
        query["text"] = "could not reconnect to the room"
        self.save_queries()
        result = self.run_evaluation()["results"][0]
        self.assertTrue(result["leakage"]["source_conditioned"])
        self.assertIn(
            "could not reconnect",
            result["mechanical_leakage"][0]["shared_query_trigrams"],
        )
        self.assertEqual(result["effective_filters"], query["filters"])

    def test_evidence_bytes_match_pinned_search_stdout_including_empty_output(self):
        for text in ("reconnect", "no_known_history_token"):
            self.dataset["queries"][0]["text"] = text
            self.save_queries()
            result = self.run_evaluation()["results"][0]["runs"][0]
            output = io.StringIO()
            with self.tool.control(self.commit) as control:
                with (
                    contextlib.closing(
                        control.readonly(self.cache_path / "memory.db")
                    ) as db,
                    contextlib.redirect_stdout(output),
                ):
                    control.memory.cmd_search(
                        db,
                        SimpleNamespace(
                            text=[text],
                            file=[],
                            symbol=[],
                            error=[],
                            trust=["maintainer", "reported"],
                            kind=["body"],
                            state=None,
                            since=None,
                            exclude=[],
                            limit=10,
                            ref=None,
                            strict=False,
                            candidate_limit=256,
                            json=False,
                        ),
                    )
            self.assertEqual(
                result["evidence_bytes_utf8"], len(output.getvalue().encode())
            )

    def test_legacy_presentation_is_used_without_repeating_selection(self):
        def unexpected_selection(*args, **kwargs):
            raise AssertionError("presentation must not rerun selection")

        legacy = SimpleNamespace(search=unexpected_selection)

        def present(db, args):
            selected = legacy.search(db, args.text)
            print("legacy format: " + selected[0]["key"])

        legacy.cmd_search = present
        rendered = self.tool.rendered_stdout(
            legacy, [{"key": "body:1"}], None, self.dataset["queries"][0], "default"
        )
        self.assertEqual(rendered, "legacy format: body:1\n")
        self.assertIs(legacy.search, unexpected_selection)

    def test_metrics_count_unjudged_without_calling_them_false_positives(self):
        result = self.tool.metrics(
            [
                {"key": "body:8", "thread": 8},
                {"key": "comment:1", "thread": 8},
                {"key": "body:1", "thread": 1},
            ],
            [{"key": "body:1", "thread": 1}],
        )
        self.assertEqual(result["document_reciprocal_rank"], 1 / 3)
        self.assertEqual(result["thread_reciprocal_rank"], 1 / 2)
        self.assertEqual(
            result["document_known_positive_recall"], {"1": 0, "3": 1, "5": 1, "10": 1}
        )
        self.assertEqual(result["unjudged_keys"], ["body:8", "comment:1"])
        self.assertNotIn("false_positives", result)
        limited = self.tool.metrics(
            [{"key": "body:1", "thread": 1}],
            [{"key": "body:1", "thread": 1}],
            limit=1,
        )
        self.assertIsNone(limited["document_known_positive_recall"]["10"])

    def test_untrusted_json_errors_do_not_echo_input_or_overwrite_output(self):
        bad = self.dir / "bad.json"
        bad.write_text('{"private source": 1, "private source": 2}')
        with self.assertRaises(self.tool.Invalid):
            self.tool.read_json(bad)
        bad.write_text('{"value":NaN}')
        with self.assertRaises(self.tool.Invalid):
            self.tool.read_json(bad)
        output = self.dir / "existing.json"
        output.write_text("keep")
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            result = self.tool.main(
                [
                    "run",
                    "--cache",
                    str(self.cache_path),
                    "--queries",
                    str(self.queries),
                    "--queries-sha256",
                    self.digest,
                    "--control-commit",
                    self.commit,
                    "--output",
                    str(output),
                ]
            )
        self.assertEqual(result, 1)
        self.assertEqual(errors.getvalue(), "retrieval evaluation failed: Invalid\n")
        self.assertEqual(output.read_text(), "keep")
