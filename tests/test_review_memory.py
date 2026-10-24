#!/usr/bin/env python3
"""Contract tests for the codetrial-review skill's project memory.

Each case serves `memory.py` a small repository's history from a fake `gh`
that reads one JSON document of endpoint answers, so a case states exactly
what GitHub said: the listings, each PR's detail, and what a later poll or
sweep sees after an edit or a deletion.
"""

import importlib.util
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "support", Path(__file__).with_name("support.py")
)
support = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)

MEMORY = support.ROOT / ".claude" / "skills" / "codetrial-review" / "memory.py"
REPO = "repos/sysprog21/codetrial"

# Answers by endpoint, the query string dropped but for what the sync must ask:
# a listing needs its order, issues need state=all, and who may push is asked
# for with permission=push, so a request that loses one fails as GitHub's 422.
# A listing asked for with since= is answered from "<path>?since" when the
# case has one, the way a poll sees only what changed. Anything else fails as
# GitHub's 404 does, and an answer {"fail": "..."} fails with that line, as a 410 or a 502 does.
FAKE_GH = """#!{python}
import json, os, sys
args = sys.argv[1:]
path = args[1]
data = json.load(open(os.environ["FAKE_GH_DATA"]))
key = path.split("?")[0]
tail = key.split("/", 3)[-1]
order = "sort=updated&direction=asc"
needs = {{"issues": ["state=all", order], "issues/comments": [order],
          "pulls/comments": [order], "collaborators": ["permission=push"]}}
for need in needs.get(tail, []):
    if need not in path:
        sys.stderr.write("HTTP 422: the request lost " + need + "\\n")
        sys.exit(1)
if "since=" in path and key + "?since" in data:
    key += "?since"
with open(os.environ["FAKE_GH_DATA"] + ".calls", "a") as log:
    log.write(path + "\\n")
if key not in data:
    sys.stderr.write("HTTP 404: Not Found (" + path + ")\\n")
    sys.exit(1)
# An answer {{"fail": "<first stderr line>"}} is a failed call, as a 410 or 502.
if isinstance(data[key], dict) and set(data[key]) == {{"fail"}}:
    sys.stderr.write(data[key]["fail"] + "\\n")
    sys.exit(1)
print(json.dumps(data[key]))
"""


def user(login, kind="User"):
    return {"login": login, "type": kind}


def issue(number, title, body, assoc="OWNER", login="maint", pr=False, **extra):
    out = {
        "number": number,
        "title": title,
        "body": body,
        "user": user(login),
        "author_association": assoc,
        "state": extra.get("state", "open"),
        "html_url": "https://github.com/sysprog21/codetrial/issues/{}".format(number),
        "created_at": "2026-01-0{}T00:00:00Z".format(number % 9 + 1),
        "updated_at": extra.get("updated", "2026-02-01T00:00:00Z"),
        "closed_at": None,
    }
    # pr is a merge time, None for a PR not merged, False for an issue.
    if pr is not False:
        out["pull_request"] = {"merged_at": pr}
    return out


def comment(cid, number, body, login="maint", assoc="OWNER", kind="User", **extra):
    out = {
        "id": cid,
        "body": body,
        "user": user(login, kind),
        "author_association": assoc,
        "html_url": "https://github.com/sysprog21/codetrial/pull/{}#c{}".format(
            number, cid
        ),
        "created_at": extra.get("created", "2026-02-02T00:00:00Z"),
        "updated_at": extra.get("updated", "2026-02-02T00:00:00Z"),
    }
    for key in ("side", "in_reply_to_id"):
        if key in extra:
            out[key] = extra[key]
    if "path" in extra:
        out.update(
            pull_request_url="https://api.github.com/{}/pulls/{}".format(REPO, number),
            path=extra["path"],
            line=extra.get("line", 2),
            original_commit_id="c0ffee{}".format(cid),
            diff_hunk=extra.get("hunk", "@@ -1,2 +1,2 @@\n keep\n+let done = true;"),
        )
    else:
        out["issue_url"] = "https://api.github.com/{}/issues/{}".format(REPO, number)
    return out


def detail(number, merged, files, state="closed"):
    return {
        "{}/pulls/{}".format(REPO, number): {
            "merged_at": merged,
            "merge_commit_sha": "m{}".format(number) * 4,
            "head": {"sha": "h{}".format(number)},
            "base": {"sha": "b{}".format(number)},
            "changed_files": len(files),
            "state": state,
        },
        "{}/pulls/{}/reviews".format(REPO, number): [
            {
                "id": 900 + number,
                "state": "CHANGES_REQUESTED",
                "body": "Keep the retry bounded by the cost contract.",
                "user": user("maint"),
                "author_association": "OWNER",
                "html_url": "https://github.com/sysprog21/codetrial/pull/{}#r".format(
                    number
                ),
                "submitted_at": "2026-02-03T00:00:00Z",
                "commit_id": "h{}".format(number),
            }
        ],
        "{}/pulls/{}/files".format(REPO, number): files,
        "{}/pulls/{}/commits".format(REPO, number): [
            {"sha": "k{}".format(number) * 5, "commit": {"message": "Fix it\n\nwhy"}}
        ],
    }


def changed(path, patch):
    return {
        "filename": path,
        "status": "modified",
        "additions": 1,
        "deletions": 1,
        "patch": patch,
    }


# Issue #1 reports a reconnect failure. PR #2 fixed it and was merged. PR #3
# tried another way and was closed without merging. PR #4 is open on the same
# file. A bot commented on #2.
HISTORY = {
    "{}/issues".format(REPO): [
        issue(
            1,
            "Reconnect loop after wifi drops",
            'The page logs "could not reconnect to the room" forever.',
            assoc="NONE",
            login="student",
            state="closed",
        ),
        issue(
            2,
            "Bound the reconnect retries",
            "Closes #1. Caps `retry_reconnect`.",
            pr="2026-02-04T00:00:00Z",
            state="closed",
        ),
        issue(
            3,
            "Retry reconnect forever",
            "Another take on #1.",
            assoc="CONTRIBUTOR",
            login="helper",
            pr=None,
            state="closed",
        ),
        issue(
            4,
            "Show the reconnect countdown",
            "Touches the same loop.",
            assoc="CONTRIBUTOR",
            login="helper",
            pr=None,
        ),
    ],
    "{}/issues/comments".format(REPO): [
        comment(11, 1, "Confirmed on Firefox too.", login="student", assoc="NONE"),
        comment(12, 2, "LGTM", login="cubic-dev-ai[bot]", assoc="NONE", kind="Bot"),
        comment(
            13,
            3,
            "We cap retries on purpose: see `retry_reconnect` and the "
            "provider cost contract.",
            login="maint",
        ),
    ],
    "{}/pulls/comments".format(REPO): [
        comment(
            21,
            2,
            "Hold `RECONNECT_LIMIT` at three; the cost doc counts each retry.",
            path="src/livekit.rs",
            line=2,
        ),
        comment(22, 3, "Unbounded retries bill forever.", path="src/gone.rs", line=2),
    ],
}
for number, merged, files in (
    (
        2,
        "2026-02-04T00:00:00Z",
        [
            changed(
                "src/livekit.rs",
                "@@ -1,2 +1,2 @@ fn retry_reconnect()\n-loop {}\n+for _ in 0..RECONNECT_LIMIT {}",
            )
        ],
    ),
    (3, None, [changed("src/gone.rs", "@@ -1 +1 @@\n-x\n+loop {}")]),
    (4, None, [changed("src/livekit.rs", "@@ -1 +1 @@\n-a\n+b")]),
):
    HISTORY.update(detail(number, merged, files))


class MemoryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "bin").mkdir()
        support.write_executable(
            self.dir / "bin" / "gh", FAKE_GH.format(python=sys.executable)
        )
        self.data = self.dir / "gh.json"
        self.answer(HISTORY)
        # The code as a review checks it: src/livekit.rs still holds the line
        # a reviewer commented on; src/gone.rs was deleted since.
        self.repo = self.dir / "repo"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "src" / "livekit.rs").write_text("keep\nlet done = true;\n")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("commit", "-qm", "base")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )

    def answer(self, data):
        self.data.write_text(json.dumps(data))

    def answer_with(self, listing, *items):
        """The repository's history with items added to one listing."""
        history = dict(HISTORY)
        history[REPO + listing] = HISTORY[REPO + listing] + list(items)
        self.answer(history)

    def run_memory(self, *args, ok=True):
        env = os.environ | {
            "PATH": "{}:{}".format(self.dir / "bin", os.environ["PATH"]),
            "FAKE_GH_DATA": str(self.data),
            "CODETRIAL_MEMORY": str(self.dir / "memory.db"),
        }
        result = subprocess.run(
            [sys.executable, str(MEMORY), *args],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def memory_module(self):
        """memory.py loaded in this process, for cases that patch gh or now."""
        spec = importlib.util.spec_from_file_location("review_memory", MEMORY)
        memory = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(memory)
        return memory

    def calls(self):
        log = Path(str(self.data) + ".calls")
        return log.read_text().splitlines() if log.exists() else []


class RepositoryTests(MemoryCase):
    def snapshot(self, path=None):
        with sqlite3.connect(path or self.dir / "memory.db") as db:
            return (
                db.execute("PRAGMA user_version").fetchone()[0],
                list(db.iterdump()),
            )

    def test_every_command_rejects_another_repository_without_touching_history(self):
        self.run_memory("backfill")
        summary = self.dir / "summary.md"
        summary.write_text("Retries stay bounded.")
        self.run_memory(
            "decisions",
            "add",
            "--topic",
            "retries",
            "--summary-file",
            str(summary),
            "--path",
            "src/livekit.rs",
            "--source",
            "review_comment:21",
        )
        before, calls = self.snapshot(), self.calls()
        foreign = json.loads(
            json.dumps(HISTORY).replace("sysprog21/codetrial", "other/project")
        )
        foreign["repos/other/project/issues"][0]["body"] = "Foreign history."
        self.answer(foreign)
        for command in (
            ("backfill",),
            ("poll",),
            ("detail",),
            ("sweep",),
            ("status",),
            ("search", "retries"),
            ("fixes", "--text", "reconnect"),
            ("show", "body:2"),
            ("why", "src/livekit.rs:2"),
            ("context", str(self.dir / "missing-bundle")),
            ("decisions", "drop", "--id", "1"),
        ):
            with self.subTest(command=command):
                result = self.run_memory("--repo", "other/project", *command, ok=False)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("sysprog21/codetrial", result.stderr)
                self.assertIn("other/project", result.stderr)
                self.assertEqual(self.calls(), calls)
                self.assertEqual(self.snapshot(), before)

    def test_repository_case_changes_keep_the_same_store(self):
        self.run_memory("backfill")
        self.assertIn(
            "#2 ",
            self.run_memory(
                "--repo", "SysProg21/CodeTrial", "search", "retries"
            ).stdout,
        )
        self.run_memory("--repo", "SysProg21/CodeTrial", "poll")
        self.assertTrue(all("SysProg21" not in url for url in self.calls()))

    def test_separate_database_can_store_another_repository(self):
        self.run_memory("backfill")
        original = self.snapshot()
        data = {
            key.replace(REPO, "repos/other/project"): value
            for key, value in HISTORY.items()
        }
        data = json.loads(
            json.dumps(data).replace("sysprog21/codetrial", "other/project")
        )
        self.answer(data)
        other = self.dir / "other.db"
        self.run_memory("--db", str(other), "--repo", "other/project", "backfill")
        self.assertIn(
            "#2 ",
            self.run_memory(
                "--db", str(other), "--repo", "other/project", "search", "retries"
            ).stdout,
        )
        self.assertEqual(self.snapshot(), original)
        self.answer(HISTORY)
        self.assertIn("#2 ", self.run_memory("search", "retries").stdout)

    def test_unbound_legacy_history_is_checked_before_a_version_rebuild(self):
        # Make a pre-binding store directly from its published schema rather
        # than depending on the new identity table's representation.
        memory = self.memory_module()
        path = self.dir / "legacy.db"
        with sqlite3.connect(path) as db:
            db.executescript(memory.KEPT + memory.DERIVED)
            db.execute("DROP TABLE repository")
            db.execute(
                "INSERT INTO raw VALUES (?, ?, ?, ?, ?)",
                (
                    "issue:1",
                    1,
                    "issue",
                    json.dumps(HISTORY[REPO + "/issues"][0]),
                    "2026-01-01T00:00:00Z",
                ),
            )
            db.execute("PRAGMA user_version = 0")
        before, calls = self.snapshot(path), self.calls()
        result = self.run_memory(
            "--db", str(path), "--repo", "other/project", "status", ok=False
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("sysprog21/codetrial", result.stderr)
        self.assertIn("other/project", result.stderr)
        self.assertEqual(self.calls(), calls)
        self.assertEqual(self.snapshot(path), before)
        self.assertIn(
            "#1 ", self.run_memory("--db", str(path), "search", "wifi").stdout
        )

    def test_legacy_mixed_or_unidentifiable_history_is_never_adopted(self):
        memory = self.memory_module()
        for kind in ("mixed", "unknown"):
            with self.subTest(kind=kind):
                path = self.dir / (kind + ".db")
                with sqlite3.connect(path) as db:
                    db.executescript(memory.KEPT + memory.DERIVED)
                    db.execute("DROP TABLE repository")
                    if kind == "mixed":
                        db.executemany(
                            "INSERT INTO raw VALUES (?, ?, ?, ?, ?)",
                            [
                                (
                                    "issue:1",
                                    1,
                                    "issue",
                                    json.dumps(HISTORY[REPO + "/issues"][0]),
                                    "2026-01-01T00:00:00Z",
                                ),
                                (
                                    "issue_comment:88",
                                    1,
                                    "issue_comment",
                                    json.dumps(comment(88, 1, "Other source.")).replace(
                                        "sysprog21/codetrial", "other/project"
                                    ),
                                    "2026-01-01T00:00:00Z",
                                ),
                            ],
                        )
                    else:
                        db.execute(
                            "INSERT INTO history (key, body, reason, superseded) "
                            "VALUES (?, ?, ?, ?)",
                            (
                                "body:1",
                                "Saved discussion.",
                                "deleted",
                                "2026-01-01T00:00:00Z",
                            ),
                        )
                before, calls = self.snapshot(path), self.calls()
                result = self.run_memory("--db", str(path), "status", ok=False)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(self.snapshot(path), before)
                self.assertEqual(self.calls(), calls)

    def test_legacy_case_variant_progress_resumes_the_same_repository(self):
        memory = self.memory_module()
        for phase in ("walk", "backfill_detail"):
            with self.subTest(phase=phase):
                path = self.dir / (phase + ".db")
                source = HISTORY[REPO + "/issues"][0]
                progress = (
                    json.dumps(
                        {
                            "repo": "SysProg21/CodeTrial",
                            "since": None,
                            "started": "2026-01-01T00:00:00Z",
                            "listing": 1,
                            "cursor": memory.start(None),
                        }
                    )
                    if phase == "walk"
                    else "SysProg21/CodeTrial"
                )
                with sqlite3.connect(path) as db:
                    db.executescript(memory.KEPT + memory.DERIVED)
                    db.execute("DROP TABLE repository")
                    db.execute(
                        "INSERT INTO raw VALUES (?, ?, ?, ?, ?)",
                        (
                            "issue:1",
                            1,
                            "issue",
                            json.dumps(source),
                            "2026-01-01T00:00:00Z",
                        ),
                    )
                    db.execute(
                        "INSERT INTO sync_state VALUES (?, ?, ?)",
                        (phase, progress, "2026-01-01T00:00:00Z"),
                    )
                self.answer(
                    {REPO + "/issues/comments": [], REPO + "/pulls/comments": []}
                )
                calls = len(self.calls())
                self.run_memory("--db", str(path), "backfill")
                self.assertTrue(
                    all(
                        "/issues/comments?" in url or "/pulls/comments?" in url
                        for url in self.calls()[calls:]
                    )
                )
                self.assertIn(
                    "#1 ", self.run_memory("--db", str(path), "search", "wifi").stdout
                )

    def test_qualified_closing_references_use_the_stores_repository(self):
        memory = self.memory_module()
        db = memory.connect(":memory:", "other/project")
        try:
            source = json.loads(
                json.dumps(HISTORY[REPO + "/issues"][1]).replace(
                    "sysprog21/codetrial", "other/project"
                )
            )
            source["body"] = (
                "Closes other/project#1. "
                "Closes https://github.com/other/project/issues/3. "
                "Closes sysprog21/codetrial#4. "
                "Closes https://github.com/sysprog21/codetrial/issues/9."
            )
            memory.store_raw(db, "issue:2", 2, "issue", source)
            memory.rebuild(db, 2)
            self.assertEqual(
                {
                    tuple(row)
                    for row in db.execute("SELECT src, dst FROM thread_closes")
                },
                {(2, 1), (2, 3)},
            )
        finally:
            db.close()


class SyncTests(MemoryCase):
    def test_linked_issue_edits_and_deletion_refresh_fix_search(self):
        self.run_memory("backfill")
        edited = dict(HISTORY)
        edited["{}/issues?since".format(REPO)] = [
            issue(
                1,
                "Spectraldisconnect",
                "A newly understood symptom.",
                state="closed",
                updated="2026-03-01T00:00:00Z",
            )
        ]
        edited["{}/issues/comments?since".format(REPO)] = []
        edited["{}/pulls/comments?since".format(REPO)] = []
        self.answer(edited)
        self.run_memory("poll")
        self.assertIn(
            "#2 merged", self.run_memory("fixes", "--text", "Spectraldisconnect").stdout
        )
        edited["{}/issues".format(REPO)] = HISTORY["{}/issues".format(REPO)][1:]
        self.answer(edited)
        self.run_memory("sweep")
        self.assertIn(
            "no close precedent",
            self.run_memory("fixes", "--text", "Spectraldisconnect").stdout,
        )

    def test_pass_checkpoint_does_not_skip_an_edit_between_listings(self):
        memory = self.memory_module()
        for initial_pass in ("backfill", "poll"):
            with self.subTest(initial_pass=initial_pass):
                db = memory.connect(":memory:")
                original = issue(
                    1, "Before", "original", updated="2026-01-01T00:00:00Z"
                )
                edited = issue(1, "After", "edited", updated="2026-01-02T00:01:00Z")
                late_comment = comment(
                    55, 1, "Later activity", updated="2026-01-02T00:03:00Z"
                )
                first_listing = True

                def api(path, paginate=False):
                    nonlocal first_listing
                    if "/collaborators?" in path:
                        return []
                    if "/issues?" in path:
                        if first_listing:
                            first_listing = False
                            return [original]
                        since = path.split("since=", 1)[1]
                        return [edited] if since <= edited["updated_at"] else []
                    if "/issues/comments?" in path:
                        return [late_comment]
                    if "/pulls/comments?" in path:
                        return []
                    self.fail("unexpected endpoint: " + path)

                args = SimpleNamespace(repo="sysprog21/codetrial", max=None)
                memory.mark(db, "poll", "2026-01-01T00:00:00Z")
                # The issue changes after its listing was read. A later comment
                # arrives more than the overlap window after that unseen edit.
                with patch.object(memory, "gh", side_effect=api):
                    with patch.object(
                        memory, "now", return_value="2026-01-02T00:00:00Z"
                    ):
                        getattr(memory, "cmd_" + initial_pass)(db, args)
                    with patch.object(
                        memory, "now", return_value="2026-01-02T00:05:00Z"
                    ):
                        memory.cmd_poll(db, args)
                self.assertEqual(
                    db.execute("SELECT title FROM threads WHERE number = 1").fetchone()[
                        0
                    ],
                    "After",
                )
                db.close()

    def test_comment_only_poll_marks_detail_stale_but_overlap_does_not(self):
        self.run_memory("backfill")
        edited = dict(HISTORY)
        edited["{}/issues?since".format(REPO)] = []
        edited["{}/issues/comments?since".format(REPO)] = []
        edited["{}/pulls/comments?since".format(REPO)] = [
            comment(
                21,
                2,
                "The review changed.",
                path="src/livekit.rs",
                updated="2026-03-01T00:00:00Z",
            )
        ]
        self.answer(edited)
        self.assertIn("1 PRs need detail", self.run_memory("poll").stdout)
        self.run_memory("detail")
        self.assertIn("0 PRs need detail", self.run_memory("poll").stdout)

    def test_sweep_retires_deleted_reviews(self):
        self.run_memory("backfill")
        gone = dict(HISTORY)
        gone["{}/pulls/2/reviews".format(REPO)] = []
        self.answer(gone)
        self.run_memory("sweep")
        self.assertIn("was deleted", self.run_memory("show", "review:902").stdout)

    def test_backfill_indexes_detail_without_changing_body_history(self):
        self.run_memory("backfill")
        self.assertIn("#2 ", self.run_memory("search", "src/livekit.rs").stdout)
        with sqlite3.connect(self.dir / "memory.db") as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM docs_fts JOIN documents d "
                    "ON d.id = docs_fts.rowid WHERE d.key = 'body:2' "
                    "AND docs_fts MATCH ?",
                    ('"src/livekit.rs"',),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                db.execute(
                    "SELECT commit_sha FROM documents WHERE key = 'body:2'"
                ).fetchone()[0],
                "m2" * 4,
            )
            self.assertEqual(
                db.execute("SELECT count(*) FROM history").fetchone()[0], 0
            )

    def test_title_only_edit_refreshes_search_without_body_history(self):
        self.run_memory("backfill")
        edited = dict(HISTORY)
        edited["{}/issues?since".format(REPO)] = [
            issue(
                1,
                "newname",
                HISTORY[REPO + "/issues"][0]["body"],
                assoc="NONE",
                login="student",
                state="closed",
                updated="2026-03-01T00:00:00Z",
            )
        ]
        edited["{}/issues/comments?since".format(REPO)] = []
        edited["{}/pulls/comments?since".format(REPO)] = []
        self.answer(edited)
        self.run_memory("poll")
        self.assertIn("#1 ", self.run_memory("search", "newname").stdout)
        self.assertIn("no matches", self.run_memory("search", "wifi").stdout)
        with sqlite3.connect(self.dir / "memory.db") as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM history").fetchone()[0], 0
            )
            self.assertEqual(
                db.execute(
                    "SELECT updated FROM documents WHERE key = 'body:1'"
                ).fetchone()[0],
                "2026-03-01T00:00:00Z",
            )

    def test_backfill_keeps_every_pr_with_its_detail_and_trust(self):
        self.run_memory("backfill")
        status = json.loads(self.run_memory("status").stdout)
        self.assertEqual((status["threads"], status["prs"]), (4, 3))
        # Merged, closed unmerged and open alike got their detail.
        self.assertEqual(status["prs_needing_detail"], 0)
        hits = self.run_memory("search", "retries").stdout
        self.assertIn("by maint (maintainer)", hits)
        self.assertIn("#3 pr [closed]", hits)
        # A bot is left out unless asked for.
        self.assertNotIn("cubic", self.run_memory("search", "LGTM").stdout)
        self.assertIn(
            "(machine)", self.run_memory("search", "LGTM", "--trust", "machine").stdout
        )

    def test_poll_reads_only_what_changed_and_marks_its_pr_stale(self):
        self.run_memory("backfill")
        before = len(self.calls())
        edited = dict(HISTORY)
        edited["{}/issues/comments?since".format(REPO)] = [
            comment(
                13,
                3,
                "We cap retries; closed in favour of the bounded fix.",
                updated="2026-03-01T00:00:00Z",
            )
        ]
        edited["{}/issues?since".format(REPO)] = [
            issue(
                3,
                "Retry reconnect forever",
                "Another take on #1.",
                assoc="CONTRIBUTOR",
                login="helper",
                pr=None,
                state="closed",
                updated="2026-03-01T00:00:00Z",
            )
        ]
        edited["{}/pulls/comments?since".format(REPO)] = []
        self.answer(edited)
        out = self.run_memory("poll").stdout
        self.assertIn("poll: 1 threads changed, 1 PRs need detail", out)
        # Every listing is incremental; the one other call asks who may push.
        self.assertTrue(
            all("since=" in c or "/collaborators?" in c for c in self.calls()[before:])
        )
        # The closed-unmerged PR is refreshed like any other.
        self.run_memory("detail")
        self.assertTrue(any(c.startswith(REPO + "/pulls/3/") for c in self.calls()))
        self.assertIn("bounded fix", self.run_memory("search", "favour").stdout)
        shown = self.run_memory("show", "issue_comment:13").stdout
        self.assertIn("an earlier version was edited", shown)

    def test_sweep_retires_what_github_no_longer_lists(self):
        self.run_memory("backfill")
        gone = dict(HISTORY)
        gone["{}/pulls/comments".format(REPO)] = HISTORY[
            "{}/pulls/comments".format(REPO)
        ][:1]
        self.answer(gone)
        self.assertIn("sweep: 1 items gone", self.run_memory("sweep").stdout)
        self.assertEqual(
            "no matches", self.run_memory("search", "Unbounded").stdout.splitlines()[-1]
        )
        self.assertIn(
            "was deleted", self.run_memory("show", "review_comment:22").stdout
        )

    def test_sweep_retires_every_deleted_comment_of_one_thread(self):
        sibling = comment(23, 3, "Retries cost money.", path="src/gone.rs", line=4)
        self.answer_with("/pulls/comments", sibling)
        self.run_memory("backfill")
        gone = dict(HISTORY)
        gone["{}/pulls/comments".format(REPO)] = HISTORY[
            "{}/pulls/comments".format(REPO)
        ][:1]
        self.answer(gone)
        self.assertIn("sweep: 2 items gone", self.run_memory("sweep").stdout)
        self.assertIn(
            "was deleted", self.run_memory("show", "review_comment:23").stdout
        )

    def test_poll_before_backfill_is_refused(self):
        result = self.run_memory("poll", ok=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("run backfill first", result.stderr)


class RulesTests(MemoryCase):
    def test_interrupted_listings_resume_committed_pages_after_reopening(self):
        for command in ("backfill", "poll"):
            with self.subTest(command=command):
                memory = self.memory_module()
                path = self.dir / (command + "-resume.db")
                args = SimpleNamespace(repo="sysprog21/codetrial", max=None)
                original = issue(
                    1, "Original", "original", updated="2026-01-01T00:00:00Z"
                )
                source = {
                    REPO + "/issues": [original],
                    REPO + "/issues/comments": [],
                    REPO + "/pulls/comments": [],
                }
                asked = []
                interrupt = False

                def api(url, paginate=False):
                    nonlocal interrupt
                    asked.append(url)
                    if "/collaborators?" in url:
                        return []
                    endpoint = url.split("?", 1)[0]
                    self.assertIn(endpoint, source)
                    page = int(re.search(r"[?&]page=(\d+)", url).group(1))
                    size = int(re.search(r"[?&]per_page=(\d+)", url).group(1))
                    if (
                        interrupt
                        and endpoint.endswith("/issues/comments")
                        and page == 2
                    ):
                        interrupt = False
                        raise memory.RateLimited("spent")
                    since = url.split("since=", 1)[1] if "since=" in url else ""
                    items = [
                        item for item in source[endpoint] if item["updated_at"] >= since
                    ]
                    return items[(page - 1) * size : page * size]

                db = memory.connect(str(path))
                with (
                    patch.object(memory, "gh", side_effect=api),
                    patch.object(
                        memory,
                        "LISTINGS",
                        tuple(
                            (kind, endpoint, 2) for kind, endpoint, _ in memory.LISTINGS
                        ),
                    ),
                ):
                    if command == "poll":
                        with patch.object(
                            memory, "now", return_value="2026-01-01T00:00:00Z"
                        ):
                            memory.cmd_backfill(db, args)
                    source[REPO + "/issues/comments"] = [
                        comment(
                            cid,
                            1,
                            "Durable comment {}".format(cid),
                            updated="2026-02-01T00:00:00Z",
                        )
                        for cid in (11, 12, 13)
                    ]
                    asked.clear()
                    interrupt = True
                    with patch.object(
                        memory, "now", return_value="2026-02-01T00:00:00Z"
                    ):
                        with self.assertRaises(memory.RateLimited):
                            getattr(memory, "cmd_" + command)(db, args)
                    db.close()
                    db = memory.connect(str(path))
                    try:
                        self.assertEqual(
                            {row["key"] for row in memory.search(db, "Durable")},
                            {"issue_comment:11", "issue_comment:12"},
                        )
                        self.assertEqual(
                            db.execute("SELECT count(*) FROM raw").fetchone()[0], 3
                        )
                        # A poll cannot satisfy a request to read all history.
                        if command == "poll":
                            with self.assertRaises(memory.Fail):
                                memory.cmd_backfill(db, args)
                        source[REPO + "/issues"] = [
                            issue(
                                1, "Catchup", "original", updated="2026-02-01T00:05:00Z"
                            )
                        ]
                        asked.clear()
                        with patch.object(
                            memory, "now", return_value="2026-02-02T00:00:00Z"
                        ):
                            getattr(memory, "cmd_" + command)(db, args)
                        self.assertFalse(
                            any(url.startswith(REPO + "/issues?") for url in asked)
                        )
                        comments = [
                            url
                            for url in asked
                            if url.startswith(REPO + "/issues/comments?")
                        ]
                        self.assertIn("page=2", comments[0])
                        self.assertEqual(
                            {row["key"] for row in memory.search(db, "Durable")},
                            {
                                "issue_comment:11",
                                "issue_comment:12",
                                "issue_comment:13",
                            },
                        )
                        # The next poll must start from the original pass time,
                        # even though resuming the pass took a whole day.
                        with patch.object(
                            memory, "now", return_value="2026-02-03T00:00:00Z"
                        ):
                            memory.cmd_poll(db, args)
                        self.assertEqual(
                            [row["key"] for row in memory.search(db, "Catchup")],
                            ["body:1"],
                        )
                    finally:
                        db.close()

    def test_backfill_resumes_detail_without_replaying_finished_listings(self):
        memory = self.memory_module()
        path = self.dir / "detail-resume.db"
        args = SimpleNamespace(repo="sysprog21/codetrial", max=None)
        detail_calls = 0

        def limited_api(url, paginate=False):
            nonlocal detail_calls
            if "/collaborators?" in url:
                return []
            if re.search(r"/pulls/\d", url):
                detail_calls += 1
                if detail_calls > 4:
                    raise memory.RateLimited("spent")
            return HISTORY.get(url.split("?", 1)[0], [])

        db = memory.connect(str(path))
        with patch.object(memory, "gh", side_effect=limited_api):
            with self.assertRaises(memory.RateLimited):
                memory.cmd_backfill(db, args)
        db.close()
        db = memory.connect(str(path))
        try:
            self.assertEqual(len(memory.stale_prs(db)), 2)

            def resume_api(url, paginate=False):
                self.assertRegex(url, r"/pulls/\d")
                return HISTORY.get(url.split("?", 1)[0], [])

            with patch.object(memory, "gh", side_effect=resume_api):
                memory.cmd_backfill(db, args)
            self.assertEqual(memory.stale_prs(db), [])
            self.assertIn(
                "body:2", {row["key"] for row in memory.search(db, "reconnect")}
            )
        finally:
            db.close()

    def test_who_may_push_decides_trust_and_a_change_reaches_every_document(self):
        pushers = dict(HISTORY)
        # helper wears the COLLABORATOR badge; only maint may push.
        pushers[REPO + "/issues/comments"] = HISTORY[REPO + "/issues/comments"] + [
            comment(14, 4, "Triage note.", login="helper", assoc="COLLABORATOR")
        ]
        pushers[REPO + "/collaborators"] = [{"login": "maint"}]
        self.answer(pushers)
        self.run_memory("backfill")
        self.assertIn(
            "by helper (reported)", self.run_memory("search", "Triage").stdout
        )
        # helper is given push access: the next poll re-derives trust.
        pushers[REPO + "/collaborators"] = [{"login": "maint"}, {"login": "helper"}]
        for listing in ("issues", "issues/comments", "pulls/comments"):
            pushers["{}/{}?since".format(REPO, listing)] = []
        self.answer(pushers)
        self.run_memory("poll")
        self.assertIn(
            "by helper (maintainer)", self.run_memory("search", "Triage").stdout
        )

    def test_kind_file_and_empty_text_mean_what_they_say(self):
        self.run_memory("backfill")
        comments = self.run_memory(
            "search", "--kind", "review_comment", "--limit", "20"
        ).stdout
        self.assertIn("review_comment by", comments)
        self.assertNotIn("review:changes_requested", comments)
        reviews = self.run_memory("search", "--kind", "review").stdout
        self.assertIn("review:changes_requested", reviews)
        self.assertNotIn("review_comment by", reviews)
        by_dir = self.run_memory("search", "--file", "src/").stdout
        self.assertIn("#2 ", by_dir)
        self.assertIn("#3 ", by_dir)
        self.assertIn("no matches", self.run_memory("search", "x").stdout)

    def test_sweep_keeps_what_a_listing_missed_but_github_still_serves(self):
        self.run_memory("backfill")
        missed = dict(HISTORY)
        missed[REPO + "/pulls/comments"] = []
        # 21 is still there when asked for directly; 22 is gone.
        missed[REPO + "/pulls/comments/21"] = HISTORY[REPO + "/pulls/comments"][0]
        self.answer(missed)
        self.assertIn("sweep: 1 items gone", self.run_memory("sweep").stdout)
        self.assertIn("three", self.run_memory("show", "review_comment:21").stdout)

    def test_a_rate_limit_naming_an_id_with_404_is_still_a_rate_limit(self):
        memory = self.memory_module()

        def refused(line):
            return SimpleNamespace(returncode=1, stdout="", stderr=line + "\n")

        limited = refused("gh: API rate limit exceeded for user ID 8340412. (HTTP 403)")
        with patch.object(memory.subprocess, "run", return_value=limited):
            with self.assertRaises(memory.RateLimited):
                memory.gh("repos/x/issues/1")
            # Never read as a deletion, which a sweep would act on.
            with self.assertRaises(memory.RateLimited):
                memory.gone_upstream("x", "issue:1", 1)
        missing = refused("gh: Not Found (HTTP 404)")
        with patch.object(memory.subprocess, "run", return_value=missing):
            with self.assertRaisesRegex(memory.Fail, "not found"):
                memory.gh("repos/x/issues/1")

    def test_a_ref_that_is_not_a_commit_is_refused_not_read_as_gone(self):
        self.run_memory("backfill")
        out = self.run_memory("search", "auth", "--ref", "no-such-ref", ok=False)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("not a commit", out.stderr)

    def test_a_retired_thread_leaves_no_signals_behind(self):
        self.run_memory("backfill")
        tables = ("thread_files", "thread_symbols", "thread_errors", "thread_commits")

        def counts():
            with sqlite3.connect(self.dir / "memory.db") as db:
                found = {
                    t: db.execute(
                        "SELECT count(*) FROM {} WHERE thread IN (1, 2, 3)".format(t)
                    ).fetchone()[0]
                    for t in tables
                }
                found["thread_closes"] = db.execute(
                    "SELECT count(*) FROM thread_closes WHERE src IN (1, 2, 3)"
                ).fetchone()[0]
            return found

        # The threads retired below have a row in each table between them (an
        # error in #1, a closing line in #2, files and commits in the PRs), or
        # the test says nothing.
        for table, n in counts().items():
            with self.subTest(table=table, before=True):
                self.assertGreater(n, 0)
        gone = dict(HISTORY)
        gone[REPO + "/issues"] = [
            i for i in HISTORY[REPO + "/issues"] if i["number"] > 3
        ]
        self.answer(gone)
        self.run_memory("sweep")
        for table, n in counts().items():
            with self.subTest(table=table):
                self.assertEqual(n, 0)
        self.assertIn("no matches", self.run_memory("search", "Unbounded").stdout)

    def test_decisions_refuse_what_they_cannot_do(self):
        self.run_memory("backfill")
        summary = self.dir / "summary.md"
        summary.write_text("Bounded.")
        self.run_memory(
            "decisions",
            "add",
            "--topic",
            "t",
            "--summary-file",
            str(summary),
            "--path",
            "src/web",
            "--source",
            "review_comment:21",
            "--source",
            "review_comment:21",
        )
        listed = self.run_memory("decisions", "list", "--ref", "HEAD").stdout
        self.assertIn("sources review_comment:21\n", listed)
        # A directory governs what is under it, not what merely starts alike.
        self.assertEqual(
            "",
            self.run_memory(
                "decisions", "list", "--ref", "HEAD", "--path", "src/webhook.rs"
            ).stdout,
        )
        self.assertIn(
            "no decision 99",
            self.run_memory("decisions", "drop", "--id", "99", ok=False).stderr,
        )
        self.assertIn(
            "drop needs --id", self.run_memory("decisions", "drop", ok=False).stderr
        )

    def test_a_store_from_another_version_is_rebuilt_from_raw(self):
        self.run_memory("backfill")
        with sqlite3.connect(self.dir / "memory.db") as db:
            db.execute("DELETE FROM docs_fts")
            db.execute("PRAGMA user_version = 0")
        # No GitHub call: the rebuild reads only what is stored.
        self.answer({})
        self.assertIn("#2 ", self.run_memory("search", "RECONNECT_LIMIT").stdout)

    def test_listing_resumes_tied_pages_without_losing_a_shifted_item(self):
        memory = self.memory_module()
        stamp = "2026-01-01T00:00:00Z"
        # Both a short final page and a full page crossing into a newer stamp
        # must validate the boundary before declaring the old bucket complete.
        for count in (3, 4):
            with self.subTest(count=count):
                items = [{"id": i, "updated_at": stamp} for i in range(1, count + 1)]
                moved = False
                asked = []

                def api(path):
                    nonlocal moved
                    asked.append(path)
                    self.assertLess(len(asked), 20)
                    since = path.split("since=")[1] if "since=" in path else ""
                    page = int(re.search(r"[?&]page=(\d+)", path).group(1))
                    # Editing an item already read shifts an unread item backward
                    # across the numbered page boundary in this old timestamp bucket.
                    if page == 2 and not moved:
                        items[0]["updated_at"] = "2026-02-01T00:00:00Z"
                        moved = True
                    newer = sorted(
                        (dict(x) for x in items if x["updated_at"] >= since),
                        key=lambda x: (x["updated_at"], x["id"]),
                    )
                    return newer[(page - 1) * 2 : page * 2]

                cursor = {"since": None, "page": 1}
                got = set()
                with patch.object(memory, "gh", side_effect=api):
                    while cursor is not None:
                        # Reopen the iterator from its saved cursor after every batch,
                        # including a page whose boundary still needs validation.
                        batch, cursor = next(
                            memory.listing_pages("o/r", "issues?", 2, cursor)
                        )
                        got.update(x["id"] for x in batch)
                        cursor = json.loads(json.dumps(cursor))
                self.assertEqual(got, set(range(1, count + 1)))

    def test_listing_pages_by_key_and_steps_through_one_timestamp(self):
        memory = self.memory_module()
        stamp = "2026-01-01T00:00:0{}Z".format
        items = [{"id": i, "updated_at": stamp(i // 2)} for i in range(5)]
        asked = []

        def api(path, paginate=False):
            asked.append(path)
            since = path.split("since=")[1] if "since=" in path else ""
            page = int(re.search(r"[?&]page=(\d+)", path).group(1))
            newer = [x for x in items if x["updated_at"] >= since]
            return newer[(page - 1) * 2 : page * 2]

        with patch.object(memory, "gh", side_effect=api):
            got = [x["id"] for x in memory.listing("o/r", "issues?", 2)]
        self.assertEqual(got, [0, 1, 2, 3, 4])
        # Ids 0 and 1 share a stamp that fills a page: the next asks for page 2.
        self.assertIn("page=2", asked[1])

    def test_a_tie_whose_order_keeps_changing_stops_instead_of_looping(self):
        memory = self.memory_module()
        stamp = "2026-01-01T00:00:00Z"
        flips = [0]

        def api(path):
            # Two full pages of one timestamp, in an order that changes on
            # every read of page 1.
            flips[0] += 1
            # Inside the fake, so a missing cap fails here instead of hanging.
            self.assertLess(flips[0], 40, "the restart cap never stopped it")
            ids = [1, 2, 3, 4] if flips[0] % 2 else [2, 1, 4, 3]
            page = int(re.search(r"[?&]page=(\d+)", path).group(1))
            return [{"id": i, "updated_at": stamp} for i in ids][
                (page - 1) * 2 : page * 2
            ]

        with patch.object(memory, "gh", side_effect=api):
            with self.assertRaises(memory.Fail) as raised:
                list(memory.listing("o/r", "issues?", 2, stamp))
        self.assertIn("keeps changing", str(raised.exception))
        self.assertLess(flips[0], 40)

    def test_a_pr_that_keeps_failing_does_not_pin_backfill_to_detail(self):
        memory = self.memory_module()
        db = memory.connect(":memory:")

        def api(path, paginate=False):
            if "/collaborators?" in path:
                return []
            if "/pulls/3" in path:
                raise memory.Fail("gh api failed: HTTP 502")
            return HISTORY.get(path.split("?")[0], [])

        args = SimpleNamespace(repo="sysprog21/codetrial", max=None)
        with patch.object(memory, "gh", side_effect=api):
            memory.cmd_backfill(db, args)
        # #3 stays stale for the next detail pass, but the backfill is over:
        # the next one reads the listings again rather than only details.
        self.assertEqual(memory.stale_prs(db), [3])
        self.assertIsNone(memory.mark(db, "backfill_detail"))
        db.close()


class MeterTests(MemoryCase):
    class Stream(io.StringIO):
        def __init__(self, tty):
            super().__init__()
            self.tty = tty

        def isatty(self):
            return self.tty

    def test_a_log_gets_one_line_per_step_and_no_redraws(self):
        memory = self.memory_module()
        out = self.Stream(tty=False)
        meter = memory.Meter(stream=out, every=3600)
        for i in range(1, 51):
            meter.step("fetching PR detail", i, 50, "#{}".format(i))
        meter.step("rebuilding threads", 1, 9)
        meter.stop()
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("memory.py: fetching PR detail  1/50"))
        self.assertNotIn("\r", out.getvalue())

    def test_a_terminal_redraws_in_place_and_clears_before_a_line_that_stays(self):
        memory = self.memory_module()
        out = self.Stream(tty=True)
        meter = memory.Meter(stream=out, interval=0.01)
        meter.step("reading issues", 3, detail="page 1")
        # Until a few frames are drawn, by the count rather than a fixed wait.
        deadline = time.monotonic() + 10
        while out.getvalue().count("\r\033[K") < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        # The line that stays goes to the same terminal: the spinner's line is
        # cleared right before it, never after.
        meter.say("poll: 1 threads changed", err=True)
        meter.stop()
        drawn = out.getvalue()
        self.assertIn("reading issues  3  page 1  0:00", drawn)
        said = drawn.index("poll: 1 threads changed\n")
        self.assertTrue(drawn[:said].endswith("\r\033[K"))
        # The last thing written to the terminal leaves the line empty.
        self.assertTrue(drawn.endswith("\r\033[K"))

    def test_a_long_step_says_about_how_long_is_left(self):
        memory = self.memory_module()
        out = self.Stream(tty=False)
        meter = memory.Meter(stream=out, every=0)
        with patch.object(memory.time, "monotonic", side_effect=[100.0, 160.0]):
            meter.step("fetching PR detail", 0, 200)
            meter.step("fetching PR detail", 50, 200)
        self.assertIn("1:00  about 3 min left", out.getvalue())
        # Rounded up, in fives under a minute and a half, then in minutes.
        self.assertEqual(
            [memory.duration(x) for x in (1, 7, 89, 90, 181)],
            ["5 s", "10 s", "90 s", "2 min", "4 min"],
        )


class ReviewFixTests(MemoryCase):
    """The defects a review of this skill found, each held to its fix."""

    def test_an_older_store_keeps_working_and_a_deletion_keeps_its_provenance(self):
        self.run_memory("backfill")
        # history as an earlier version made it: four columns, no provenance.
        with sqlite3.connect(self.dir / "memory.db") as db:
            db.execute("DROP TABLE history")
            db.execute(
                "CREATE TABLE history (key TEXT, body TEXT, reason TEXT, superseded TEXT)"
            )
            db.execute("PRAGMA user_version = 3")
        gone = dict(HISTORY)
        gone[REPO + "/pulls/comments"] = HISTORY[REPO + "/pulls/comments"][:1]
        self.answer(gone)
        self.assertIn("sweep: 1 items gone", self.run_memory("sweep").stdout)
        shown = self.run_memory("show", "review_comment:22").stdout
        self.assertIn("was deleted", shown)
        self.assertIn("by maint (maintainer)", shown)
        self.assertIn("pull/3#c22", shown)
        self.assertIn("on src/gone.rs:2 at c0ffee22", shown)

    def test_sweep_retires_deleted_and_transferred_issues_and_keeps_a_502(self):
        self.run_memory("backfill")
        swept = dict(HISTORY)
        swept[REPO + "/issues"] = [
            i for i in HISTORY[REPO + "/issues"] if i["number"] not in (1, 4)
        ]
        swept[REPO + "/issues/1"] = {"fail": "HTTP 410: This issue was deleted"}
        swept[REPO + "/issues/4"] = dict(
            HISTORY[REPO + "/issues"][3],
            repository_url="https://api.github.com/repos/other/project",
        )
        swept[REPO + "/issues/comments"] = [
            c for c in HISTORY[REPO + "/issues/comments"] if c["id"] != 13
        ]
        swept[REPO + "/issues/comments/13"] = {"fail": "HTTP 502: Bad Gateway"}
        self.answer(swept)
        # Both issues; #4's review goes with its thread, with no call of its own.
        before = len(self.calls())
        self.assertIn("sweep: 2 items gone", self.run_memory("sweep").stdout)
        self.assertNotIn(REPO + "/pulls/4/reviews/904", self.calls()[before:])
        self.assertIn("no matches", self.run_memory("search", "wifi").stdout)
        self.assertIn("no matches", self.run_memory("search", "countdown").stdout)
        # A 502 says nothing about whether the comment is gone: it stays, on a
        # thread the sweep did not retire, so the sweep asked about it.
        self.assertIn(REPO + "/issues/comments/13", self.calls()[before:])
        self.assertEqual(self.run_memory("show", "issue_comment:13").returncode, 0)

    def test_an_error_with_another_number_still_matches(self):
        self.answer_with(
            "/issues/comments",
            comment(15, 1, 'The log says "request failed with status 429".'),
        )
        self.run_memory("backfill")
        out = self.run_memory(
            "search", "--error", "request failed with status 503"
        ).stdout
        self.assertIn("#1 issue", out)

    def test_a_comment_on_a_deleted_line_is_judged_by_that_line(self):
        self.answer_with(
            "/pulls/comments",
            comment(
                23,
                2,
                "Keep the auth check.",
                path="src/livekit.rs",
                side="LEFT",
                hunk="@@ -1,2 +1,1 @@\n keep\n-require_auth();",
            ),
        )
        self.run_memory("backfill")
        out = self.run_memory("search", "auth", "--ref", "HEAD").stdout
        self.assertIn("now: the commented line has changed in src/livekit.rs", out)

    def test_context_finds_a_file_named_in_conversation_and_says_when_empty(self):
        bundle = self.dir / "bundle"
        bundle.mkdir()
        (bundle / "manifest.json").write_text(
            json.dumps({"pr": 9, "files": [{"path": "src/gone.rs"}]})
        )
        self.run_memory("context", str(bundle), "--ref", "HEAD")
        self.assertIn(
            "The project memory is empty", (bundle / "history.md").read_text()
        )
        self.answer_with(
            "/issues/comments",
            comment(16, 3, "Leave src/gone.rs alone until the cost doc changes."),
        )
        self.run_memory("backfill")
        self.run_memory("context", str(bundle), "--ref", "HEAD")
        text = (bundle / "history.md").read_text()
        self.assertIn("Memory: 4 threads, last polled", text)
        self.assertIn("Leave src/gone.rs alone", text)

    def test_export_says_which_comment_replies(self):
        self.answer_with(
            "/pulls/comments",
            comment(24, 2, "Agreed.", path="src/livekit.rs", in_reply_to_id=21),
        )
        self.run_memory("backfill")
        rows = [
            json.loads(line)
            for line in self.run_memory(
                "export", "--source", "review_comment"
            ).stdout.splitlines()
        ]
        replies = {row["key"]: row["reply"] for row in rows}
        self.assertEqual(replies["review_comment:24"], True)
        self.assertEqual(replies["review_comment:21"], False)
        self.assertTrue(all(row["source"] == "review_comment" for row in rows))

    def test_an_unstable_tie_leaves_no_cursor_behind(self):
        memory = self.memory_module()
        db = memory.connect(":memory:")
        stamp = "2026-01-01T00:00:00Z"
        reads = [0]

        def api(path, paginate=False):
            if "/collaborators?" in path:
                return []
            reads[0] += 1
            self.assertLess(reads[0], 60, "the restart cap never stopped it")
            ids = [1, 2, 3, 4] if reads[0] % 2 else [2, 1, 4, 3]
            page = int(re.search(r"[?&]page=(\d+)", path).group(1))
            items = [issue(i, "Tie", "Same second.", updated=stamp) for i in ids]
            return items[(page - 1) * 2 : page * 2]

        args = SimpleNamespace(repo="sysprog21/codetrial", max=None)
        memory.mark(db, "poll", stamp)
        with patch.object(memory, "LISTINGS", (("issue", "issues?state=all&", 2),)):
            with patch.object(memory, "gh", side_effect=api):
                with self.assertRaises(memory.TieUnstable):
                    memory.cmd_poll(db, args)
        self.assertIsNone(memory.mark(db, "walk"))
        db.close()

    def in_child(self, body, *args):
        """Run body with memory.py loaded, in a process of its own: a case that
        replaces sys.stdout or sys.stderr cannot do it here, where the test
        runner's other threads share both."""
        code = (
            "import importlib.util, sys\n"
            "from types import SimpleNamespace\n"
            "spec = importlib.util.spec_from_file_location('m', sys.argv[1])\n"
            "memory = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(memory)\n" + body
        )
        return subprocess.run(
            [sys.executable, "-c", code, str(MEMORY), *args],
            capture_output=True,
            text=True,
            cwd=self.repo,
        )

    def test_nothing_reaches_stdout_with_stderr_closed(self):
        result = self.in_child(
            "sys.stderr = None\n"
            "meter = memory.Meter()\n"
            "meter.step('rebuilding threads', 1, 2)\n"
            "meter.stop()\n"
            # A diagnostic, as default_ref says one with no remote.
            "memory.git = lambda *a: SimpleNamespace(stdout='', returncode=1)\n"
            "assert memory.default_ref() == 'HEAD'\n"
        )
        self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)

    def test_an_added_line_starting_with_plus_is_not_a_file_header(self):
        memory = self.memory_module()
        bundle = self.dir / "bundle"
        bundle.mkdir()
        # In a markdown file, an added "++" line, then a code span: still prose.
        (bundle / "pr.diff").write_text(
            "diff --git a/docs/a.md b/docs/a.md\n--- a/docs/a.md\n+++ b/docs/a.md\n"
            "@@ -1 +1,2 @@\n keep\n+++ counter;\n+See `retry_reconnect`.\n"
            "diff --git a/src/a.rs b/src/a.rs\n--- a/src/a.rs\n+++ b/src/a.rs\n"
            "@@ -1 +1,2 @@\n keep\n+fn send_if_due() {}\n"
        )
        symbols, _ = memory.diff_signals(str(bundle))
        self.assertEqual(symbols, {"send_if_due"})

    def test_punctuation_in_a_query_does_not_empty_an_all_words_search(self):
        self.run_memory("backfill")
        out = self.run_memory("search", "retries ... bill").stdout
        self.assertIn("#3 pr", out)
        # Through the AND leg: a document that has only "retries" would be
        # found by the OR retry, and is not found here.
        self.assertNotIn("#1 issue", out)

    def test_an_author_github_no_longer_has_does_not_stop_a_pass(self):
        ghost = dict(HISTORY)
        ghost[REPO + "/issues"] = HISTORY[REPO + "/issues"] + [
            dict(issue(5, "From a deleted account", "Still here."), user=None)
        ]
        self.answer(ghost)
        self.run_memory("backfill")
        self.assertIn("#5 issue", self.run_memory("search", "deleted account").stdout)

    def test_prose_is_not_read_as_code_names(self):
        memory = self.memory_module()
        self.assertEqual(memory.symbols_in("see `let them type`"), set())
        self.assertEqual(memory.symbols_in("see `send_if_due` here"), {"send_if_due"})

    def test_a_moved_comment_keeps_its_line_and_commit_together(self):
        moved = comment(25, 2, "Still about the loop.", path="src/livekit.rs", line=9)
        moved.update(commit_id="nowsha", original_line=2)
        self.answer_with("/pulls/comments", moved)
        self.run_memory("backfill")
        out = self.run_memory("show", "review_comment:25").stdout
        self.assertIn("on src/livekit.rs:2 at c0ffee25", out)

    def test_two_sync_passes_never_run_at_once(self):
        memory = self.memory_module()
        path = str(self.dir / "locked.db")
        held = memory.pass_lock(path)
        try:
            with self.assertRaises(memory.Fail) as raised:
                memory.pass_lock(path)
        finally:
            held.close()
        self.assertIn("another backfill, poll, detail or sweep", str(raised.exception))

    def test_the_first_pass_on_a_fresh_clone_takes_the_lock(self):
        memory = self.memory_module()
        path = str(self.dir / "fresh" / "codetrial-review" / "memory.db")
        memory.pass_lock(path).close()
        self.assertTrue(os.path.exists(path + ".lock"))

    def test_the_last_version_wins_a_tie_in_history(self):
        self.run_memory("backfill")
        with sqlite3.connect(self.dir / "memory.db") as db:
            for body in ("older", "newer"):
                db.execute(
                    "INSERT INTO history (key, body, reason, superseded) "
                    "VALUES ('issue_comment:99', ?, 'deleted', '2026-03-01T00:00:00Z')",
                    (body,),
                )
        self.assertIn("> newer", self.run_memory("show", "issue_comment:99").stdout)

    def test_ctrl_c_says_progress_is_kept_and_exits_130(self):
        result = self.in_child(
            "def interrupted(db, args):\n"
            "    raise KeyboardInterrupt\n"
            "memory.cmd_status = interrupted\n"
            "print(memory.main(['--db', sys.argv[2], 'status']))\n",
            str(self.dir / "m.db"),
        )
        self.assertEqual(result.stdout.strip(), "130", result.stderr)
        self.assertIn("running the same command again resumes", result.stderr)


class QueryTests(MemoryCase):
    def setUp(self):
        super().setUp()
        self.run_memory("backfill")

    def test_decision_detects_an_old_edit_discovered_after_creation(self):
        summary = self.dir / "summary.md"
        summary.write_text("Retries stay bounded.")
        self.run_memory(
            "decisions",
            "add",
            "--topic",
            "retries",
            "--summary-file",
            str(summary),
            "--path",
            "src/livekit.rs",
            "--source",
            "review_comment:21",
        )
        edited = dict(HISTORY)
        edited["{}/issues?since".format(REPO)] = []
        edited["{}/issues/comments?since".format(REPO)] = []
        edited["{}/pulls/comments?since".format(REPO)] = [
            comment(
                21,
                2,
                "The ruling has changed.",
                path="src/livekit.rs",
                updated="2026-03-01T00:00:00Z",
            )
        ]
        self.answer(edited)
        self.run_memory("poll")
        self.assertIn(
            "stale: source review_comment:21 changed",
            self.run_memory("decisions", "stale", "--ref", "HEAD").stdout,
        )

    def test_filters_narrow_by_file_symbol_and_trust(self):
        by_file = self.run_memory("search", "--file", "src/livekit.rs").stdout
        self.assertIn("#2 ", by_file)
        self.assertNotIn("#3 ", by_file)
        by_symbol = self.run_memory(
            "search", "cap", "--symbol", "retry_reconnect"
        ).stdout
        self.assertIn("#3 ", by_symbol)
        reported = self.run_memory("search", "Firefox", "--trust", "maintainer").stdout
        self.assertIn("no matches", reported)

    def test_a_part_of_a_name_finds_the_name(self):
        # RECONNECT_LIMIT is indexed with its parts as well.
        self.assertIn("RECONNECT_LIMIT", self.run_memory("search", "limit").stdout)

    def test_a_hit_says_whether_its_line_is_still_in_the_code(self):
        out = self.run_memory("search", "bill", "--ref", "HEAD").stdout
        self.assertIn("now: src/gone.rs is gone", out)
        out = self.run_memory("search", "three", "--ref", "HEAD").stdout
        self.assertIn("now: the commented line is still in src/livekit.rs", out)

    def test_fixes_finds_the_merged_fix_and_admits_no_match(self):
        out = self.run_memory("fixes", "--text", "reconnect loop forever").stdout
        self.assertIn("#2 merged 2026-02-04", out)
        self.assertNotIn("#3 ", out)
        none = self.run_memory("fixes", "--text", "dark mode toggle contrast").stdout
        self.assertIn("no close precedent", none)

    def test_context_is_bounded_and_keeps_provenance(self):
        bundle = self.dir / "bundle"
        bundle.mkdir()
        (bundle / "manifest.json").write_text(
            json.dumps(
                {
                    "pr": 9,
                    "head": "h9",
                    "base": "nope",
                    "closes": [1],
                    "files": [{"path": "src/livekit.rs"}],
                }
            )
        )
        (bundle / "pr.json").write_text(
            json.dumps({"title": "Reconnect again", "body": "Closes #1"})
        )
        (bundle / "pr.diff").write_text(
            "diff --git a/src/livekit.rs b/src/livekit.rs\n--- a/src/livekit.rs\n"
            "+++ b/src/livekit.rs\n@@ -1 +1 @@ fn retry_reconnect()\n"
            '-a\n+let e = "could not reconnect to the room";\n'
        )
        out = self.run_memory("context", str(bundle), "--ref", "HEAD").stdout
        text = (bundle / "history.md").read_text()
        self.assertEqual(out.strip(), str(bundle / "history.md"))
        self.assertIn("Untrusted historical evidence", text)
        self.assertIn("## Issue #1 this closes", text)
        self.assertIn("## Maintainers on src/livekit.rs", text)
        self.assertIn("@c0ffee21", text)
        self.assertIn("## Open work on the same files", text)
        self.assertIn("#4 pr Show the reconnect countdown (open", text)
        self.assertIn("#2 merged 2026-02-04", text)
        # Most binding first: what it closes and open work before file history.
        order = [
            text.index(h)
            for h in (
                "## Issue #1",
                "## Open work",
                "## How similar",
                "## Maintainers on",
            )
        ]
        self.assertEqual(order, sorted(order))
        small = self.run_memory(
            "context", str(bundle), "--ref", "HEAD", "--max-chars", "400"
        ).stdout
        self.assertTrue(small)
        self.assertLess(len((bundle / "history.md").read_text()), 900)
        self.assertIn("at the size limit", (bundle / "history.md").read_text())

    def test_a_decision_cites_maintainers_and_goes_stale_with_its_source(self):
        summary = self.dir / "summary.md"
        summary.write_text("Reconnect retries stay bounded; each one is billed.")
        refused = self.run_memory(
            "decisions",
            "add",
            "--topic",
            "retries",
            "--summary-file",
            str(summary),
            "--path",
            "src/livekit.rs",
            "--source",
            "issue_comment:11",
            ok=False,
        )
        self.assertIn("not a maintainer's", refused.stderr)
        self.run_memory(
            "decisions",
            "add",
            "--topic",
            "retries",
            "--summary-file",
            str(summary),
            "--path",
            "src/livekit.rs",
            "--source",
            "review_comment:21",
            "--ref",
            "HEAD",
        )
        self.assertIn(
            "[current]", self.run_memory("decisions", "list", "--ref", "HEAD").stdout
        )
        self.assertEqual(
            "", self.run_memory("decisions", "stale", "--ref", "HEAD").stdout
        )
        # The source is edited upstream: the summary no longer stands on it.
        edited = dict(HISTORY)
        edited["{}/pulls/comments?since".format(REPO)] = [
            comment(
                21,
                2,
                "Hold it at five now.",
                path="src/livekit.rs",
                updated="2099-01-01T00:00:00Z",
            )
        ]
        edited["{}/issues?since".format(REPO)] = []
        edited["{}/issues/comments?since".format(REPO)] = []
        self.answer(edited)
        self.run_memory("poll")
        self.assertIn(
            "stale: source review_comment:21 changed",
            self.run_memory("decisions", "stale", "--ref", "HEAD").stdout,
        )


if __name__ == "__main__":
    unittest.main()
