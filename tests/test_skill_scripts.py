#!/usr/bin/env python3
"""Contract tests for the scripts the agent skills under .claude/skills run.

The sweeps read GitHub through `gh`, so each case serves them one GraphQL page
from a fake `gh` and asserts on the lists they print. The reference checker runs
in a scratch repository whose Makefile, workflow and skills are written here, so
a case names exactly what resolves and what does not.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "support", Path(__file__).with_name("support.py")
)
support = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)

ROOT = support.ROOT
SKILLS = ROOT / ".claude" / "skills"
ISSUE_SWEEP = SKILLS / "codetrial-triage" / "issue-sweep.sh"
PR_SWEEP = SKILLS / "codetrial-triage" / "pr-sweep.sh"
SKILL_REFS = ROOT / "scripts" / "check-skill-refs.py"

# A gh that fails every call, so only a script's own check can refuse first.
FAKE_GH_FAILS = "#!/bin/sh\nexit 1\n"

# The viewer and the one account with push access. The sweeps ask `gh` for
# both before the GraphQL page; the runs listing is empty, so no PR is held.
FAKE_GH = """#!/bin/sh
case "$*" in
  "api user --jq .login") echo viewer ;;
  *collaborators*) printf 'maint\\nviewer\\n' ;;
  *actions/runs*) cat "$FAKE_RUNS" ;;
  "api graphql --paginate"*) cat "$FAKE_PAGE" ;;
  "run list "*) [ -n "${FAKE_MAIN_FAILED:-}" ] || exit 1; echo 500 ;;
  "run view 500 "*) printf '%s\\n' "$FAKE_MAIN_FAILED" ;;
  "api graphql"*) cat "$FAKE_AGAIN" ;;
  "pr view 404 "*) echo "no such pull request" >&2; exit 1 ;;
  "pr view"*) printf '{"pr":%s,"state":"MERGED","merge":"m%s"}\\n' "$3" "$3" ;;
  *) echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
"""

# Enough prose and a pasted --version line that no lint fires unless a case
# asks for it.
FILLER = (
    "\n\ncodetrial 0.1.0 (abc1234), and enough words that the body is not "
    "taken for an empty one."
)


def ago(days):
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def comment(login, days, body="Please post the `--version` line."):
    return {
        "author": {"__typename": "User", "login": login},
        "authorAssociation": "MEMBER" if login == "maint" else "NONE",
        "createdAt": ago(days),
        "body": body,
    }


def issue(number, body, **fields):
    node = {
        "number": number,
        "title": f"Interview stalls in case {number}",
        "body": body,
        "createdAt": ago(30),
        "authorAssociation": "NONE",
        "author": {"login": "reporter"},
        "issueType": {"name": "Bug"},
        "userContentEdits": {"totalCount": 0, "nodes": []},
        "assignees": {"totalCount": 0, "nodes": []},
        "assigned": {"totalCount": 0, "nodes": []},
        "comments": {"totalCount": 0, "nodes": []},
        "timelineItems": {"totalCount": 0, "nodes": []},
    }
    return node | fields


def listed(*nodes):
    return {"totalCount": len(nodes), "nodes": list(nodes)}


def checked(*runs):
    """A head pushed ten days ago whose check runs ended as (name, conclusion).

    A conclusion of None is a run still in progress.
    """
    return {
        "nodes": [
            {
                "commit": {
                    "committedDate": ago(10),
                    "checkSuites": {"nodes": [{"createdAt": ago(10)}]},
                    "statusCheckRollup": {
                        "contexts": {
                            "nodes": [
                                {
                                    "__typename": "CheckRun",
                                    "databaseId": index,
                                    "name": name,
                                    "status": "IN_PROGRESS"
                                    if conclusion is None
                                    else "COMPLETED",
                                    "conclusion": conclusion,
                                }
                                for index, (name, conclusion) in enumerate(runs)
                            ]
                        }
                    },
                }
            }
        ]
    }


def pull(number, **fields):
    node = {
        "number": number,
        "title": f"Fix case {number}",
        "isDraft": False,
        "authorAssociation": "CONTRIBUTOR",
        "headRefOid": f"head{number}",
        "mergeable": "MERGEABLE",
        "baseRefName": "main",
        "updatedAt": ago(1),
        "headRefName": "topic",
        "headRepository": {"nameWithOwner": "contributor/codetrial"},
        "author": {"login": "contributor"},
        "closingIssuesReferences": listed(),
        "files": listed(),
        # The head was pushed ten days ago, which dates every request.
        "commits": checked(),
        "reviews": listed(),
        "comments": listed(),
        "reviewThreads": listed(),
        "timelineItems": {"nodes": []},
    }
    return node | fields


def review(login, state, days, body="", oid="head1"):
    return {
        "id": f"R_{login}",
        "author": {"__typename": "User", "login": login},
        "authorAssociation": "MEMBER",
        "state": state,
        "submittedAt": ago(days),
        "isMinimized": False,
        "body": body,
        "commit": {"oid": oid},
        "comments": {"totalCount": 0},
    }


def request(node_id, days, body):
    return {
        "id": node_id,
        "author": {"__typename": "User", "login": "maint"},
        "authorAssociation": "MEMBER",
        "createdAt": ago(days),
        "isMinimized": False,
        "body": body,
    }


FIXED = "Rebase latest `main` branch and resolve conflicts."


GREEN = checked(("check", "SUCCESS"), ("gates (rust)", "SUCCESS"))


def thread(kind="User", login="maint"):
    return {
        "id": f"T_{login}",
        "isResolved": False,
        "isOutdated": False,
        "path": "web/runners.js",
        "comments": {"nodes": [{"author": {"__typename": kind, "login": login}}]},
    }


def run_fake(script, args, fake_gh, files, cwd=None, env=()):
    """Run a skill script against a fake gh, with files it or the fake reads."""
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        (scratch / "bin").mkdir()
        support.write_executable(scratch / "bin" / "gh", fake_gh)
        paths = {}
        for variable, content in files.items():
            paths[variable] = scratch / variable.lower()
            paths[variable].write_text(content)
        # An argument "$NAME" is the path of the file given as NAME.
        args = [str(paths[a[1:]]) if str(a).startswith("$") else str(a) for a in args]
        return subprocess.run(
            ["sh", str(script), *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            env=os.environ
            | {"PATH": f"{scratch / 'bin'}:{os.environ['PATH']}"}
            | {k: str(v) for k, v in paths.items()}
            | dict(env),
        )


@unittest.skipUnless(shutil.which("jq"), "jq is not installed")
class SweepCase(unittest.TestCase):
    def sweep(self, script, page, previous=None, runs=(), again=(), env=()):
        files = {
            "FAKE_PAGE": json.dumps({"data": page}),
            # gh applies --jq itself, so the fake prints one object per run.
            "FAKE_RUNS": "".join(json.dumps(run) + "\n" for run in runs),
            "FAKE_AGAIN": json.dumps(list(again)),
        }
        args = ["sysprog21/codetrial"]
        # ci-failure.sh as the PR sweep runs it: a PR in $STALE is a stale base.
        files["PR_SWEEP_CI"] = (
            'case " $STALE " in *" $1 "*) s=true ;; *) s=false ;; esac\n'
            'printf \'{"pr":%s,"head":"%s","stale_base":%s}\\n\' '
            '"$1" "${STALE_HEAD:-head$1}" "$s"\n'
        )
        if previous is not None:
            files["PREVIOUS"] = json.dumps(previous)
            args += ["7", "$PREVIOUS"]
        result = run_fake(script, args, FAKE_GH, files, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)


class IssueSweepTests(SweepCase):
    def run_issues(self, *issues):
        page = {
            "repository": {
                "issues": {"pageInfo": {"hasNextPage": False}, "nodes": list(issues)}
            }
        }
        return self.sweep(ISSUE_SWEEP, page)

    def gaps(self, body):
        lint = self.run_issues(issue(1, body))["lint"]
        return lint[0]["gaps"] if lint else []

    def test_heading_followed_by_a_higher_level_one_is_an_empty_section(self):
        self.assertIn(
            "empty_section", self.gaps("## Steps\n# Environment\nmacOS" + FILLER)
        )

    def test_heading_followed_by_its_own_subsection_is_not_empty(self):
        self.assertEqual(self.gaps("## Steps\n### First\nOpen the lobby." + FILLER), [])

    def test_comments_in_a_fenced_shell_session_are_not_headings(self):
        body = "## Steps\n```sh\n# start it\n# then\n```\nOpen the lobby."
        self.assertEqual(self.gaps(body + FILLER), [])

    def test_a_shorter_fence_inside_a_longer_one_does_not_close_it(self):
        body = "## Steps\n````md\n```\n# not a heading\n# nor this\n````\nOpen it."
        self.assertEqual(self.gaps(body + FILLER), [])

    def test_heading_on_the_last_line_is_an_empty_section(self):
        self.assertIn(
            "empty_section", self.gaps("## Steps\nOpen it." + FILLER + "\n## Logs")
        )

    def test_uppercase_hash_counts_as_a_revision(self):
        body = "Seen on build DEADBEEF1 after the reconnect, with enough prose here."
        self.assertNotIn("no_revision", self.gaps(body))

    def test_bug_without_a_revision_is_flagged(self):
        body = "The interview stalls after reconnecting, every time, on two machines."
        self.assertIn("no_revision", self.gaps(body))

    def test_unanswered_question_is_waiting(self):
        swept = self.run_issues(
            issue(1, "Body" + FILLER, comments=listed(comment("maint", 10)))
        )
        self.assertEqual([entry["issue"] for entry in swept["waiting"]], [1])

    def test_reporter_edit_hidden_by_a_later_maintainer_edit_still_answers(self):
        edits = listed(
            {"editedAt": ago(2), "editor": {"login": "maint"}},
            {"editedAt": ago(5), "editor": {"login": "reporter"}},
        )
        swept = self.run_issues(
            issue(
                1,
                "Body" + FILLER,
                comments=listed(comment("maint", 10)),
                userContentEdits=edits,
            )
        )
        self.assertEqual(swept["waiting"], [])

    def test_idle_days_is_unknown_when_no_act_of_the_assignee_was_read(self):
        swept = self.run_issues(
            issue(
                1,
                "Body" + FILLER,
                assignees=listed({"login": "student"}),
                comments=listed(comment("maint", 10, "Assigned to you.")),
            )
        )
        [claim] = swept["claimed"]
        self.assertIsNone(claim["idle_days"])
        self.assertIsNone(claim["checked_at"])
        self.assertIsNone(claim["stale"])

    def test_an_open_pr_that_closes_the_issue_is_named_on_its_entry(self):
        here = {"nameWithOwner": "sysprog21/codetrial"}
        fork = {"nameWithOwner": "someone/codetrial"}
        closing = {
            "nodes": [
                {"number": 7, "repository": here},
                {"number": 9, "repository": fork},
            ]
        }
        swept = self.run_issues(
            issue(1, "Body" + FILLER, closedByPullRequestsReferences=closing),
            issue(2, "Body" + FILLER, comments=listed(comment("maint", 10))),
            issue(
                3,
                "Body" + FILLER,
                comments=listed(comment("maint", 10)),
                closedByPullRequestsReferences=closing,
            ),
        )
        self.assertEqual(
            {e["issue"]: e["closing_prs"] for e in swept["unanswered"]}, {1: [7]}
        )
        self.assertEqual(
            {e["issue"]: e["closing_prs"] for e in swept["waiting"]}, {2: [], 3: [7]}
        )

    def test_more_closing_prs_than_read_marks_the_issue_truncated(self):
        closing = {"totalCount": 11, "nodes": []}
        swept = self.run_issues(
            issue(1, "Body" + FILLER, closedByPullRequestsReferences=closing)
        )
        self.assertEqual(swept["truncated"], [1])

    def test_more_body_edits_than_read_marks_the_issue_truncated(self):
        edits = {"totalCount": 21, "nodes": []}
        swept = self.run_issues(issue(1, "Body" + FILLER, userContentEdits=edits))
        self.assertEqual(swept["truncated"], [1])


class PrSweepTests(SweepCase):
    def run_pulls(self, *pulls, **options):
        page = {
            "repository": {
                "defaultBranchRef": {"name": "main"},
                "pullRequests": {
                    "pageInfo": {"hasNextPage": False},
                    "nodes": list(pulls),
                },
            }
        }
        return self.sweep(PR_SWEEP, page, **options)

    def grades(self, *pulls):
        return {
            entry["pr"]: entry["grade"] for entry in self.run_pulls(*pulls)["status"]
        }

    def test_status_ranks_by_size_and_splits_out_tests(self):
        files = listed(
            {"path": "web/runners.js", "additions": 2, "deletions": 1},
            {"path": "tests/browser/runners.test.js", "additions": 40, "deletions": 0},
        )
        swept = self.run_pulls(
            pull(1, additions=90, deletions=10, commits=GREEN),
            pull(2, additions=42, deletions=1, files=files, commits=GREEN),
        )
        self.assertEqual([entry["pr"] for entry in swept["status"]], [2, 1])
        self.assertEqual(
            swept["status"][0]["size"], {"total": 43, "source": 3, "tests": 40}
        )

    def test_grade_follows_what_github_records(self):
        cases = {
            "high": pull(1, commits=GREEN),
            "low": pull(2, commits=checked(("gates (rust)", "FAILURE"))),
            "blocked": pull(
                3,
                commits=GREEN,
                mergeable="CONFLICTING",
                reviewThreads=listed(thread()),
            ),
            "medium": pull(4, commits=checked(("check", "SUCCESS"))),
            "medium-high": pull(5, commits=GREEN, reviewDecision="CHANGES_REQUESTED"),
        }
        grades = self.grades(*cases.values())
        for grade, node in cases.items():
            with self.subTest(grade=grade):
                self.assertEqual(grades[node["number"]], grade)

    def test_waits_on_names_whose_move_it_is_and_review_queues_the_maintainers(self):
        cases = {
            1: (pull(1, commits=GREEN), "maintainer"),
            2: (pull(2, commits=checked(("gates (rust)", "FAILURE"))), "author"),
            3: (pull(3, commits=checked(("gates (rust)", None))), "ci"),
            4: (
                pull(
                    4,
                    commits=checked(
                        ("gates (rust)", "SUCCESS"), ("check", "ACTION_REQUIRED")
                    ),
                ),
                "approval",
            ),
            # The author pushed past the changes request: a re-review is owed.
            5: (
                pull(5, commits=GREEN, reviewDecision="CHANGES_REQUESTED"),
                "maintainer",
            ),
            6: (pull(6, commits=GREEN, reviewThreads=listed(thread())), "author"),
            7: (pull(7, commits=GREEN, isDraft=True), "author"),
            8: (pull(8, commits=GREEN, mergeable="CONFLICTING"), "author"),
            9: (pull(9, commits=checked(("check", "SUCCESS"))), "author"),
            12: (pull(12, commits=GREEN, author={"login": "viewer"}), "maintainer"),
            10: (pull(10, commits=GREEN, mergeable="UNKNOWN"), "ci"),
            # A held run outranks the conflict: nothing runs until approved.
            11: (
                pull(
                    11,
                    mergeable="CONFLICTING",
                    commits=checked(("check", "ACTION_REQUIRED")),
                ),
                "approval",
            ),
        }
        swept = self.run_pulls(
            *(node for node, _ in cases.values()),
            env={"PR_SWEEP_RETRY_DELAY": "0"},
        )
        self.assertEqual(
            {entry["pr"]: entry["waits_on"] for entry in swept["status"]},
            {number: who for number, (_, who) in cases.items()},
        )
        # The viewer's own PR waits on nobody else's review.
        self.assertEqual(sorted(swept["review"]), [1, 5])

    def test_a_stale_base_is_asked_to_rebase_under_the_conflicting_limits(self):
        failing = checked(("gates (portable)", "FAILURE"), ("check", "FAILURE"))
        swept = self.run_pulls(
            pull(1, commits=failing),
            # ci-failure.sh says its failure is its own.
            pull(2, commits=failing),
            pull(
                3, commits=failing, comments=listed(request("C3", 1, "Rebase, please."))
            ),
            pull(4, commits=failing, baseRefName="feature"),
            pull(5, commits=failing, isDraft=True),
            pull(6, commits=failing, author={"login": "viewer"}),
            pull(7, commits=failing, comments={"totalCount": 101, "nodes": []}),
            # Already listed for its conflict, so asked once.
            pull(8, commits=failing, mergeable="CONFLICTING"),
            # Green: nothing to rebase for.
            pull(9, commits=GREEN),
            env={"STALE": "1 3 4 5 6 7 8 9"},
        )
        self.assertEqual(
            sorted((entry["pr"], entry["reason"]) for entry in swept["rebase"]),
            [(1, "stale_base"), (8, "conflicts")],
        )

    def test_a_job_main_also_fails_stays_the_authors_and_says_so(self):
        # The lane runs several gates, so a shared name is not a shared cause.
        swept = self.run_pulls(
            pull(1, commits=checked(("gates (rust)", "FAILURE"), ("check", "FAILURE"))),
            env={"FAKE_MAIN_FAILED": json.dumps(["gates (rust)", "check"])},
        )
        [entry] = swept["status"]
        self.assertEqual(entry["waits_on"], "author")
        self.assertIn(
            "failing: check, gates (rust); main also fails check, gates (rust)",
            entry["why"],
        )
        self.assertEqual(swept["review"], [])

    def test_a_failed_diagnosis_never_asks_for_a_rebase(self):
        failing = checked(("gates (portable)", "FAILURE"))
        cases = {
            # Says stale, then exits non-zero: not trusted.
            "exit": 'echo \'{"pr":1,"head":"head1","stale_base":true}\'; exit 1\n',
            # Answers for another PR.
            "other pr": 'echo \'{"pr":2,"head":"head1","stale_base":true}\'\n',
            # Diagnosed a head pushed after the sweep read this one.
            "other head": 'echo \'{"pr":1,"head":"later","stale_base":true}\'\n',
        }
        for name, stub in cases.items():
            with self.subTest(name):
                page = {
                    "repository": {
                        "defaultBranchRef": {"name": "main"},
                        "pullRequests": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": [pull(1, commits=failing)],
                        },
                    }
                }
                files = {
                    "FAKE_PAGE": json.dumps({"data": page}),
                    "FAKE_RUNS": "",
                    "FAKE_AGAIN": "[]",
                    "PR_SWEEP_CI": stub,
                }
                result = run_fake(PR_SWEEP, ["sysprog21/codetrial"], FAKE_GH, files)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["rebase"], [])

    def test_moved_names_a_change_of_whose_move_it_is(self):
        node = pull(1, commits=GREEN)
        first = self.run_pulls(node)
        earlier = first | {
            "status": [entry | {"waits_on": "author"} for entry in first["status"]]
        }
        swept = self.run_pulls(node, previous=earlier)
        self.assertEqual(
            swept["moved"],
            [{"pr": 1, "changed": {"waits_on": ["author", "maintainer"]}}],
        )

    def test_refs_only_lists_issues_the_merge_leaves_open(self):
        asker = request("C1", 1, "Make the last line `Closes #7`.")
        outsider = comment("student", 1, "Closes #8 would be better.") | {
            "id": "C2",
            "isMinimized": False,
        }
        swept = self.run_pulls(
            pull(1, commits=GREEN, body="Shows the camera.\n\nRefs #141"),
            # It closes #5, so its Refs #6 stands (CONTRIBUTING.md).
            pull(
                2,
                commits=GREEN,
                body="Refs #6",
                closingIssuesReferences=listed({"number": 5}),
            ),
            pull(3, commits=GREEN, body="Refs: #7", comments=listed(asker)),
            pull(4, commits=GREEN, body="Refs #8", comments=listed(outsider)),
            # Asked about #70, which is not #7.
            pull(
                5,
                commits=GREEN,
                body="Refs #7",
                comments=listed(request("C3", 1, "Closes #70")),
            ),
            pull(6, commits=GREEN, body=None),
            pull(
                7,
                commits=GREEN,
                body="Refs sysprog21/codetrial#9\n"
                "Refs https://github.com/sysprog21/codetrial/issues/10",
                comments=listed(
                    request(
                        "C4",
                        1,
                        "Fixes https://github.com/sysprog21/codetrial/issues/10",
                    )
                ),
            ),
            pull(8, commits=GREEN, body="Refs #11, #12 and #13"),
        )
        self.assertEqual(
            swept["refs_only"],
            [
                {"pr": 1, "issue": 141, "asked": False},
                {"pr": 3, "issue": 7, "asked": True},
                {"pr": 4, "issue": 8, "asked": False},
                {"pr": 5, "issue": 7, "asked": False},
                {"pr": 7, "issue": 9, "asked": False},
                {"pr": 7, "issue": 10, "asked": True},
                {"pr": 8, "issue": 11, "asked": False},
                {"pr": 8, "issue": 12, "asked": False},
                {"pr": 8, "issue": 13, "asked": False},
            ],
        )

    def test_human_thread_lowers_the_grade_and_a_bot_thread_only_to_medium(self):
        grades = self.grades(
            pull(1, commits=GREEN, reviewThreads=listed(thread())),
            pull(2, commits=GREEN, reviewThreads=listed(thread("Bot", "cubic-dev-ai"))),
        )
        self.assertEqual(grades, {1: "low", 2: "medium"})

    def test_a_thread_the_author_opened_is_not_a_reviewer_thread(self):
        grades = self.grades(
            pull(1, commits=GREEN, reviewThreads=listed(thread(login="contributor")))
        )
        self.assertEqual(grades, {1: "high"})

    def test_a_failure_rerun_green_is_not_failing(self):
        rerun = checked(
            ("gates (rust)", "FAILURE"),
            ("gates (rust)", "SUCCESS"),
            ("check", "SUCCESS"),
        )
        self.assertEqual(self.grades(pull(1, commits=rerun)), {1: "high"})

    def test_a_failing_status_is_not_hidden_by_a_check_run_of_its_name(self):
        commits = checked(("lint", "SUCCESS"), ("gates (rust)", "SUCCESS"))
        contexts = commits["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]
        contexts["nodes"].append(
            {"__typename": "StatusContext", "context": "lint", "state": "ERROR"}
        )
        swept = self.run_pulls(pull(1, commits=commits))
        entry = swept["status"][0]
        self.assertEqual(
            (entry["checks"]["failing"], entry["grade"]), (["lint"], "low")
        )

    def test_a_pr_past_100_reviews_is_not_graded_as_fully_read(self):
        swept = self.run_pulls(
            pull(1, commits=GREEN, reviews={"totalCount": 101, "nodes": []})
        )
        entry = swept["status"][0]
        self.assertEqual(entry["grade"], "medium")
        self.assertIn("more than 100 reviews or threads, not all read", entry["why"])

    def test_a_held_run_is_not_a_failure(self):
        grades = self.grades(
            pull(
                1,
                commits=checked(
                    ("gates (rust)", "SUCCESS"), ("check", "ACTION_REQUIRED")
                ),
            )
        )
        self.assertEqual(grades, {1: "medium"})

    def test_problem_files_named_with_test_inside_a_word_are_source(self):
        files = listed(
            {
                "path": "web/problems/tightest-channel-spacing.json",
                "additions": 30,
                "deletions": 0,
            },
            {"path": "tests/test_gen_problems.py", "additions": 5, "deletions": 0},
            {"path": "web/lib.test.js", "additions": 4, "deletions": 0},
        )
        swept = self.run_pulls(pull(1, additions=39, deletions=0, files=files))
        self.assertEqual(
            swept["status"][0]["size"], {"total": 39, "source": 30, "tests": 9}
        )

    def test_one_approval_does_not_hide_another_maintainers_request(self):
        reviews = listed(
            review("maint", "CHANGES_REQUESTED", 3, "Fix the null case."),
            review("viewer", "APPROVED", 1),
        )
        grades = self.grades(
            pull(1, commits=GREEN, reviews=reviews, reviewDecision="CHANGES_REQUESTED")
        )
        self.assertEqual(grades, {1: "blocked"})

    def test_only_a_request_that_opens_with_rebase_is_a_rebase(self):
        def asked(number, body):
            request = review("maint", "CHANGES_REQUESTED", 1, body, f"head{number}")
            return pull(
                number,
                commits=GREEN,
                reviewDecision="CHANGES_REQUESTED",
                reviews=listed(request),
            )

        grades = self.grades(
            asked(1, FIXED), asked(2, "Fix the null dereference; no rebase is needed.")
        )
        self.assertEqual(grades, {1: "medium", 2: "blocked"})

    def test_adding_a_failing_check_never_raises_a_blocked_grade(self):
        grades = self.grades(
            pull(
                1,
                commits=checked(("gates (rust)", "FAILURE")),
                mergeable="CONFLICTING",
                reviewThreads=listed(thread()),
            )
        )
        self.assertEqual(grades, {1: "blocked"})

    def test_a_run_waiting_for_approval_with_no_check_run_is_held(self):
        run = {
            "id": 1,
            "name": "check",
            "head_sha": "head1",
            "head_branch": "topic",
            "head_repo": "contributor/codetrial",
            "actor": "contributor",
        }
        swept = self.run_pulls(pull(1, commits=GREEN), runs=[run])
        entry = swept["status"][0]
        self.assertEqual(
            (entry["checks"]["held"], entry["grade"]), (["check"], "medium")
        )

    def test_previous_sweep_reports_only_what_moved_and_what_left(self):
        first = self.run_pulls(
            pull(1, commits=GREEN), pull(2, commits=GREEN), pull(9, commits=GREEN)
        )
        swept = self.run_pulls(
            pull(1, commits=GREEN),
            pull(2, commits=GREEN, mergeable="CONFLICTING"),
            previous=first,
        )
        self.assertEqual(
            swept["moved"],
            [
                {
                    "pr": 2,
                    "changed": {
                        "mergeable": ["MERGEABLE", "CONFLICTING"],
                        "grade": ["high", "medium"],
                        "waits_on": ["maintainer", "author"],
                    },
                }
            ],
        )
        self.assertEqual(swept["gone"], [{"pr": 9, "state": "MERGED", "merge": "m9"}])

    def test_a_failed_lookup_marks_one_departed_pr_and_keeps_the_sweep(self):
        first = self.run_pulls(pull(1), pull(9), pull(404))
        swept = self.run_pulls(pull(1), previous=first)
        self.assertEqual(
            swept["gone"],
            [
                {"pr": 9, "state": "MERGED", "merge": "m9"},
                {"pr": 404, "state": "UNKNOWN"},
            ],
        )

    def test_a_check_finishing_moves_a_pr_even_when_its_grade_holds(self):
        waiting = checked(("gates (rust)", "SUCCESS"), ("check", None))
        decided = {"reviewDecision": "CHANGES_REQUESTED"}
        first = self.run_pulls(pull(1, commits=waiting, **decided))
        swept = self.run_pulls(pull(1, commits=GREEN, **decided), previous=first)
        self.assertEqual([entry["pr"] for entry in swept["moved"]], [1])
        # The grade holds; the move passes from CI to a re-review.
        self.assertEqual(list(swept["moved"][0]["changed"]), ["waits_on", "checks"])
        self.assertEqual(swept["moved"][0]["changed"]["waits_on"], ["ci", "maintainer"])

    def test_unknown_mergeability_is_asked_again_for_those_prs_only(self):
        swept = self.run_pulls(
            pull(1, commits=GREEN, mergeable="UNKNOWN"),
            pull(2, commits=GREEN),
            again=[{"number": 1, "mergeable": "CONFLICTING"}],
            env={"PR_SWEEP_RETRY_DELAY": "0"},
        )
        self.assertEqual(swept["unknown"], [])
        self.assertEqual(
            {entry["pr"]: entry["mergeable"] for entry in swept["status"]},
            {1: "CONFLICTING", 2: "MERGEABLE"},
        )

    def test_runs_awaiting_approval_on_old_heads_are_only_counted(self):
        def run(sha):
            return {
                "id": sha,
                "name": "check",
                "head_sha": sha,
                "head_branch": "topic",
                "head_repo": "contributor/codetrial",
                "actor": "contributor",
            }

        swept = self.run_pulls(pull(1, commits=GREEN), runs=[run("head1"), run("old")])
        self.assertEqual(
            ([entry["run"] for entry in swept["approve"]], swept["superseded"]),
            (["head1"], 1),
        )

    def test_failed_gates_line_matches_what_the_test_gate_prints(self):
        # ci-failure.sh reads the gate names from the last line test.sh prints
        # on failure; a change to that line has to break this test too.
        self.assertIn(
            'echo "failed gates:$failed"', (ROOT / "scripts" / "test.sh").read_text()
        )
        self.assertIn(
            'startswith("failed gates:")', (VERIFY / "ci-failure.sh").read_text()
        )

    def test_workflow_facts_the_review_checklist_states_still_hold(self):
        # review.md's workflow checklist says check.yml runs on pull_request
        # with contents: read, and that the only job granted contents: write
        # is release, which runs on a push to main.
        workflow = (ROOT / ".github" / "workflows" / "check.yml").read_text()
        self.assertNotIn("pull_request_target", workflow)
        self.assertNotIn("workflow_run", workflow)
        self.assertNotIn("issue_comment", workflow)
        self.assertIn("\npermissions:\n  contents: read\n", workflow)
        self.assertEqual(workflow.count("contents: write"), 1)
        release = workflow[workflow.index("\n  release:\n") :]
        release = release[: release.index("steps:")]
        self.assertIn("contents: write", release)
        self.assertIn(
            "if: github.event_name == 'push' && github.ref == 'refs/heads/main'",
            release,
        )

    def test_hook_installer_prints_the_labels_conventions_explains(self):
        # codetrial-conventions tells an agent to read make hooks' output by
        # these labels, so renaming one has to move that text too.
        installer = (ROOT / "scripts" / "install-git-hooks.sh").read_text()
        for label in ("OK", "HOOK", "KEEP", "NOTE"):
            with self.subTest(label=label):
                self.assertIn(f"printf '  {label} ", installer)

    def test_gates_prefix_matches_the_check_workflow_job_name(self):
        # The grade reads "no gates lane ran" from this prefix; renaming the
        # job in check.yml without the script would mark every PR as such.
        workflow = (ROOT / ".github" / "workflows" / "check.yml").read_text()
        self.assertIn("name: gates (", workflow)
        self.assertIn('startswith("gates (")', PR_SWEEP.read_text())

    def test_a_previous_file_that_is_not_one_sweep_is_refused(self):
        # An earlier sweep that failed while redirected leaves the file empty.
        for previous in (
            "",
            " \n\t",
            "null",
            "{}",
            "[]",
            "broken",
            "{}\n{}",
            '{"status": 1}',
            '{"status": {}}',
        ):
            with self.subTest(previous=previous):
                result = run_fake(
                    PR_SWEEP,
                    ["sysprog21/codetrial", "7", "$PREVIOUS"],
                    FAKE_GH_FAILS,
                    {"PREVIOUS": previous},
                )
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertIn("is not an earlier pr-sweep.sh output", result.stderr)

    def test_without_a_previous_sweep_moved_and_gone_are_null(self):
        swept = self.run_pulls(pull(1))
        self.assertIsNone(swept["moved"])
        self.assertIsNone(swept["gone"])

    def test_pr_with_no_commits_does_not_end_the_sweep(self):
        swept = self.run_pulls(pull(1, commits={"nodes": []}), pull(2))
        self.assertEqual([entry["number"] for entry in swept["prs"]], [1, 2])

    def test_conflicting_pr_without_a_request_is_asked_to_rebase(self):
        swept = self.run_pulls(pull(1, mergeable="CONFLICTING"))
        self.assertEqual(
            swept["rebase"],
            [{"pr": 1, "author": "contributor", "reason": "conflicts"}],
        )

    def test_current_fixed_request_is_not_repeated_or_hidden(self):
        swept = self.run_pulls(
            pull(1, mergeable="CONFLICTING", comments=listed(request("IC_1", 5, FIXED)))
        )
        self.assertEqual(swept["rebase"], [])
        self.assertEqual(swept["hide"], [])

    def test_current_hand_written_request_supersedes_an_older_fixed_one(self):
        comments = listed(
            request("IC_old", 20, FIXED),
            request("IC_new", 5, "Rebase latest `main` to pick up the zip fix."),
        )
        for mergeable in ("MERGEABLE", "CONFLICTING"):
            with self.subTest(mergeable=mergeable):
                swept = self.run_pulls(pull(1, mergeable=mergeable, comments=comments))
                self.assertEqual([entry["id"] for entry in swept["hide"]], ["IC_old"])
                self.assertEqual(swept["hide_ask"], [])
                self.assertEqual(swept["rebase"], [])


RELATED = SKILLS / "codetrial-contribute" / "related.sh"

# The viewer, one issue held by someone else, and the open PRs; gh applies
# --jq itself, so the issue view answers in the shape related.sh asks for.
FAKE_GH_RELATED = """#!/bin/sh
case "$*" in
  "api user --jq .login") echo viewer ;;
  "issue view 5 "*) echo '{"number":5,"title":"t","state":"OPEN","stateReason":"","assignees":["viewer","someone"]}' ;;
  "issue view "*) printf '{"number":%s,"title":"t","state":"OPEN","stateReason":"","assignees":[]}\n' "$3" ;;
  "pr list"*) cat "$FAKE_PRS" ;;
  *) echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
"""


def open_pr(number, author, branch, paths, closes=(), head=""):
    return {
        "number": number,
        "title": f"PR {number}",
        "author": {"login": author},
        "headRefName": branch,
        "headRefOid": head,
        "files": [{"path": path} for path in paths],
        "closingIssuesReferences": [{"number": n} for n in closes],
    }


@unittest.skipUnless(shutil.which("jq") and shutil.which("git"), "jq or git missing")
class RelatedTests(unittest.TestCase):
    def test_branch_meets_the_same_issue_shared_files_and_a_held_issue(self):
        with tempfile.TemporaryDirectory() as scratch:
            scratch = Path(scratch)
            repo = scratch / "repo"
            git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
            (repo / "web").mkdir()
            (repo / "web" / "runners.js").write_text("a\n")
            subprocess.run([*git, "add", "."], check=True)
            subprocess.run([*git, "commit", "-qm", "base"], check=True)
            subprocess.run([*git, "switch", "-qc", "topic"], check=True)
            (repo / "web" / "runners.js").write_text("b\n")
            # A qualified close counts, another repository's does not, and a
            # reference to an issue the branch also closes is a close.
            message = (
                "Fix it\n\nCloses #5\nFixes sysprog21/codetrial#12\n"
                "Fixes other/repo#9\nRefs #5\nRefs #8"
            )
            subprocess.run([*git, "commit", "-qam", message], check=True)
            head = subprocess.run(
                [*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
            ).stdout.strip()
            prs = [
                open_pr(7, "other", "x", ["web/runners.js"], closes=[5]),
                open_pr(8, "other", "y", ["web/styles.css"]),
                # Pushed under another name, found by its head commit.
                open_pr(9, "viewer", "pushed-as", ["web/runners.js"], head=head),
            ]
            result = run_fake(
                RELATED,
                [],
                FAKE_GH_RELATED,
                {"FAKE_PRS": json.dumps(prs)},
                cwd=repo,
                env={"RELATED_BASE": "main"},
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        found = json.loads(result.stdout)
        self.assertEqual(found["files"], ["web/runners.js"])
        self.assertEqual((found["closes"], found["refs"]), ([5, 12], [8]))
        self.assertEqual(
            {entry["number"]: entry["others_hold"] for entry in found["issues"]},
            {5: True, 8: False, 12: False},
        )
        self.assertEqual(found["own_pr"], 9)
        self.assertEqual([entry["pr"] for entry in found["same_issue"]], [7])
        self.assertEqual(
            found["same_files"],
            [
                {
                    "pr": 7,
                    "title": "PR 7",
                    "author": "other",
                    "shared": ["web/runners.js"],
                }
            ],
        )


VERIFY = SKILLS / "codetrial-verify"

# One PR at head "h1": a gates lane whose log names two gates, the aggregate job
# main also fails, and a check from an app outside GitHub Actions.
FAKE_GH_CI = """#!/bin/sh
case "$*" in
  "pr view 5 "*)
    while [ "$1" != --jq ]; do shift; done
    pr=${FAKE_PR:-}
    [ -n "$pr" ] || pr='{"headRefOid":"h1","files":[],"changedFiles":0}'
    printf '%s\\n' "$pr" | jq -c "$2" ;;
  "pr checks 5 "*) cat "$FAKE_CHECKS"; exit 1 ;;
  "run list "*"--event push "*) echo "500 ${FAKE_MAIN_AT:-2026-10-22T05:00:00Z}" ;;
  "run view 500 "*)
    if [ -n "${FAKE_MAIN:-}" ]; then printf '%s\\n' "$FAKE_MAIN"
    else printf '%s\\n' '[{"name":"check","conclusion":"failure"},{"name":"gates (portable)","conclusion":"success"}]'
    fi ;;
  "run view 7 --repo sysprog21/codetrial --json headSha"*) echo "h1 2026-10-22T01:00:00Z" ;;
  "run view 7 "*"--job 70 "*) cat "$FAKE_LOG" ;;
  "run view 7 "*"--job 73 "*) printf 'gates (browser)\\tlobby\\t2026-10-22T00:00:00.0Z SyntaxError: Unexpected token\\n' ;;
  "run view 7 "*"--job 74 "*) : ;;
  "run view 7 "*"--job 75 "*) echo "log expired" >&2; exit 1 ;;
  "run view 7 "*"--job 71 "*) printf 'check\\tcollect\\t2026-10-22T00:00:00.0Z ##[error]gate lanes: failure\\n' ;;
  "pr diff 5 "*) cat "$FAKE_DIFF" ;;
  *) echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
"""

# A step's first log line carries a byte order mark before its timestamp, in
# the message column, as the real log of run 39712346073 does.
BOM = "\ufeff"
CI_LOG = "".join(
    f"gates (portable)\trun\t{BOM if n == 0 else ''}2026-10-22T00:00:0{n}.0Z {line}\n"
    for n, line in enumerate(
        [
            "Ran 24 tests",
            "error: 3 vulnerabilities found!",
            "failed gates: cargo-audit indent",
            "##[error]Process completed with exit code 1.",
        ]
    )
)

# A modified file whose hunk deletes a line reading "-- x", and a deleted file.
DIFF = """\
diff --git a/web/a.js b/web/a.js
index 1..2 100644
--- a/web/a.js
+++ b/web/a.js
@@ -10,3 +10,3 @@ function f() {
 keep
--- x
+new
 tail
diff --git a/web/a b.txt b/web/a b.txt
index 1..2 100644
--- a/web/a b.txt\t
+++ b/web/a b.txt\t
@@ -1,1 +1,1 @@
-old
+new
diff --git a/web/old.js b/web/old.js
deleted file mode 100644
--- a/web/old.js
+++ /dev/null
@@ -1,1 +0,0 @@
-gone
"""


@unittest.skipUnless(shutil.which("jq"), "jq is not installed")
class VerifyScriptTests(unittest.TestCase):
    def test_ci_failure_names_the_gates_and_what_main_shares(self):
        checks = [
            {
                "name": "gates (portable)",
                "state": "FAILURE",
                "bucket": "fail",
                "link": "https://github.com/sysprog21/codetrial/actions/runs/7/job/70",
            },
            {
                "name": "check",
                "state": "FAILURE",
                "bucket": "fail",
                "link": "https://github.com/sysprog21/codetrial/actions/runs/7/job/71",
            },
            # Cancelled at its time limit: no failed step, so only the whole
            # job log shows why (#300).
            {
                "name": "gates (browser)",
                "state": "CANCELLED",
                "bucket": "cancel",
                "link": "https://github.com/sysprog21/codetrial/actions/runs/7/job/73",
            },
            # gh files a startup failure as pending; it never ran.
            {
                "name": "gates (mutants)",
                "state": "STARTUP_FAILURE",
                "bucket": "pending",
                "link": "https://github.com/sysprog21/codetrial/actions/runs/7/job/74",
            },
            # Its log can no longer be read.
            {
                "name": "gates (wasm-old)",
                "state": "FAILURE",
                "bucket": "fail",
                "link": "https://github.com/sysprog21/codetrial/actions/runs/7/job/75",
            },
            {
                "name": "cubic",
                "state": "FAILURE",
                "bucket": "fail",
                "link": "https://www.cubic.dev/pr/5",
            },
            {
                "name": "gates (rust)",
                "state": "PENDING",
                "bucket": "pending",
                "link": "https://github.com/sysprog21/codetrial/actions/runs/7/job/72",
            },
            # gh files a held check under fail; it has no log to read.
            {
                "name": "gates (wasm)",
                "state": "ACTION_REQUIRED",
                "bucket": "fail",
                "link": "https://github.com/sysprog21/codetrial/actions/runs/8/job/80",
            },
        ]
        result = run_fake(
            VERIFY / "ci-failure.sh",
            [5],
            FAKE_GH_CI,
            {"FAKE_CHECKS": json.dumps(checks), "FAKE_LOG": CI_LOG},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        found = json.loads(result.stdout)
        self.assertEqual(found["pending"], ["gates (rust)"])
        self.assertEqual(found["held"], ["gates (wasm)"])
        lane, collector, cancelled, startup, unread, app = found["failing"]
        # Log order, not alphabetical, which would put "Process" first.
        self.assertEqual(
            lane["errors"],
            ["error: 3 vulnerabilities found!", "Process completed with exit code 1."],
        )
        self.assertEqual(
            (unread["gates"], unread["errors"], unread["excerpt"]), (None, None, None)
        )
        self.assertTrue(lane["excerpt"].startswith("Ran 24 tests"), lane["excerpt"])
        self.assertEqual(cancelled["errors"], ["SyntaxError: Unexpected token"])
        self.assertEqual((startup["name"], startup["errors"]), ("gates (mutants)", []))
        self.assertEqual(
            (lane["gates"], lane["current"], lane["on_main"]),
            (["cargo-audit", "indent"], True, False),
        )
        self.assertIn("error: 3 vulnerabilities found!", lane["errors"])
        self.assertEqual((collector["name"], collector["on_main"]), ("check", True))
        self.assertEqual(app, {"name": "cubic", "link": "https://www.cubic.dev/pr/5"})
        # indent failed beside cargo-audit, so a rebase would not clear it.
        self.assertIs(found["stale_base"], False)

    def test_an_audit_failure_main_has_fixed_is_a_stale_base(self):
        checks = [
            {
                "name": name,
                "state": "FAILURE",
                "bucket": "fail",
                "link": f"https://github.com/sysprog21/codetrial/actions/runs/7/job/{job}",
            }
            for name, job in (("gates (portable)", 70), ("check", 71))
        ]

        def pr_with(*paths, changed=None):
            return json.dumps(
                {
                    "headRefOid": "h1",
                    "files": [{"path": path} for path in paths],
                    "changedFiles": len(paths) if changed is None else changed,
                }
            )

        cubic = {
            "name": "cubic",
            "state": "FAILURE",
            "bucket": "fail",
            "link": "https://www.cubic.dev/pr/5",
        }
        # main skipped the lane: absent from its failures, yet not a pass.
        skipped = json.dumps([{"name": "gates (portable)", "conclusion": "skipped"}])
        log = CI_LOG.replace("cargo-audit indent", "cargo-audit")
        cases = {
            "stale": (checks, {}, True),
            "touches Cargo": (checks, {"FAKE_PR": pr_with("Cargo.lock")}, False),
            "changes the gate": (
                checks,
                {"FAKE_PR": pr_with("scripts/test.sh")},
                False,
            ),
            "changes CI": (
                checks,
                {"FAKE_PR": pr_with(".github/workflows/check.yml")},
                False,
            ),
            # gh read 100 of 101 files, so the lockfile may be the one cut.
            "more files than read": (
                checks,
                {
                    "FAKE_PR": pr_with(
                        *(f"web/f{n}.js" for n in range(100)), changed=101
                    )
                },
                False,
            ),
            "main skipped the lane": (checks, {"FAKE_MAIN": skipped}, False),
            "a check outside Actions fails": (checks + [cubic], {}, False),
            # main last ran before the PR did, so a new advisory fails both.
            "main ran first": (
                checks,
                {"FAKE_MAIN_AT": "2026-10-21T00:00:00Z"},
                False,
            ),
            "a lane is still pending": (
                checks
                + [
                    {
                        "name": "gates (rust)",
                        "state": "IN_PROGRESS",
                        "bucket": "pending",
                        "link": "",
                    }
                ],
                {},
                False,
            ),
        }
        for name, (listed_checks, env, stale) in cases.items():
            with self.subTest(name):
                result = run_fake(
                    VERIFY / "ci-failure.sh",
                    [5],
                    FAKE_GH_CI,
                    {"FAKE_CHECKS": json.dumps(listed_checks), "FAKE_LOG": log},
                    env=env,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIs(json.loads(result.stdout)["stale_base"], stale)

    def test_a_checks_lookup_that_fails_is_not_read_as_a_clean_pr(self):
        # gh can fail without JSON; whitespace must not count as a response.
        for checks in ("", " \n\t", "null", "{}", "broken", "[]\n[]"):
            with self.subTest(checks=checks):
                result = run_fake(
                    VERIFY / "ci-failure.sh",
                    [5],
                    FAKE_GH_CI,
                    {"FAKE_CHECKS": checks, "FAKE_LOG": ""},
                )
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("could not read the checks of #5", result.stderr)

    def test_a_successful_empty_checks_lookup_is_a_clean_pr(self):
        result = run_fake(
            VERIFY / "ci-failure.sh",
            [5],
            FAKE_GH_CI,
            {"FAKE_CHECKS": "[]", "FAKE_LOG": ""},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["failing"], [])

    def test_a_pr_that_is_not_a_number_is_refused_before_any_call(self):
        for script, args in (
            ("ci-failure.sh", ["abc"]),
            ("review-anchors.sh", ["abc", "payload.json"]),
            ("post-review.sh", ["abc", "payload.json", "2026-10-22T00:00:00Z"]),
        ):
            with self.subTest(script=script):
                # The fake gh fails every call, so only the script's own
                # check can produce exit 2 and its message.
                result = run_fake(VERIFY / script, args, FAKE_GH_FAILS, {})
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("is not a pull request number", result.stderr)

    def test_review_anchors_keep_diff_lines_inline_and_move_the_rest(self):
        def note(path, line, side, text):
            return {"path": path, "line": line, "side": side, "body": text}

        payload = {
            "event": "COMMENT",
            "commit_id": "h1",
            "body": "Notes.",
            "comments": [
                note("web/a.js", 11, "RIGHT", "added line"),
                note("web/a.js", 11, "LEFT", "the deleted -- x line"),
                note("web/a.js", 12, "RIGHT", "context on the right"),
                # Left line 12 exists only if the "-- x" line was counted.
                note("web/a.js", 12, "LEFT", "context on the left"),
                note("web/old.js", 1, "LEFT", "deleted file"),
                # git appends a tab to a header path containing a space.
                note("web/a b.txt", 1, "RIGHT", "a name with a space"),
                note("web/a.js", 40, "RIGHT", "outside every hunk"),
                note("web/a.js", 11, "RIGHT", "starts outside") | {"start_line": 2},
            ],
        }
        result = run_fake(
            VERIFY / "review-anchors.sh",
            [5, "$FAKE_PAYLOAD"],
            FAKE_GH_CI,
            {"FAKE_DIFF": DIFF, "FAKE_PAYLOAD": json.dumps(payload)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        checked = json.loads(result.stdout)
        self.assertEqual(
            [c["body"] for c in checked["comments"]],
            [
                "added line",
                "the deleted -- x line",
                "context on the right",
                "context on the left",
                "deleted file",
                "a name with a space",
            ],
        )
        self.assertEqual(
            checked["body"],
            "Notes.\n\nweb/a.js:40: outside every hunk\n\nweb/a.js:11: starts outside",
        )


# The PR as post-review.sh rereads it before posting, and the post itself.
FAKE_GH_POST = """#!/bin/sh
case "$*" in
  "pr view 5 "*) echo "${FAKE_HEAD:-h1} ${FAKE_UPDATED:-T1}" ;;
  "pr diff 5 "*) cat "$FAKE_DIFF" ;;
  # Only the checked text on stdin: posting the file again would let a
  # rewrite after the checks through.
  "api -X POST repos/sysprog21/codetrial/pulls/5/reviews --input - "*)
    jq -r '"https://github.com/sysprog21/codetrial/pull/5#pullrequestreview-" + .commit_id' ;;
  *) echo "unexpected gh call: $*" >&2; exit 1 ;;
esac
"""


@unittest.skipUnless(shutil.which("jq"), "jq is not installed")
class PostReviewTests(unittest.TestCase):
    def post(self, line=11, env=(), **fields):
        payload = {
            "event": "COMMENT",
            "commit_id": "h1",
            "body": "Notes.",
            "comments": [
                {"path": "web/a.js", "line": line, "side": "RIGHT", "body": "x"}
            ],
        } | fields
        return run_fake(
            VERIFY / "post-review.sh",
            [5, "$FAKE_PAYLOAD", "T1"],
            FAKE_GH_POST,
            {"FAKE_DIFF": DIFF, "FAKE_PAYLOAD": json.dumps(payload)},
            env=env,
        )

    def test_posts_when_the_pr_is_where_the_user_read_it(self):
        result = self.post()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pullrequestreview-h1", result.stdout)

    def test_refuses_a_payload_without_a_commit_event_or_body(self):
        # Without an event GitHub would keep a pending review only its writer
        # sees; without a body it refuses the review.
        cases = {
            "commit_id": (None, "names no commit_id"),
            "event": (None, "names no event"),
            "body": ("", "has no body"),
        }
        for field, (value, said) in cases.items():
            with self.subTest(field):
                result = self.post(**{field: value})
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertIn(said, result.stderr)

    def test_refuses_a_payload_that_is_not_one_object(self):
        # The fake gh fails every call, so only the payload check can produce
        # exit 2 and its message.
        for payload in ("", " \n\t", "null", "[]", "broken", "{}\n{}"):
            with self.subTest(payload=payload):
                result = run_fake(
                    VERIFY / "post-review.sh",
                    [5, "$FAKE_PAYLOAD", "T1"],
                    FAKE_GH_FAILS,
                    {"FAKE_PAYLOAD": payload},
                )
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertIn("is not one JSON object", result.stderr)

    def test_refuses_without_posting_when_anything_moved(self):
        cases = {
            "head": ({"FAKE_HEAD": "h2"}, 11, "moved from h1 to h2"),
            # Also what a second run meets after a post: the post itself
            # moves updatedAt, so a rerun refuses instead of posting twice.
            "updated": ({"FAKE_UPDATED": "T2"}, 11, "was updated at T2"),
            "anchor": ({}, 40, "outside the diff"),
        }
        for name, (env, line, said) in cases.items():
            with self.subTest(name):
                result = self.post(line, env)
                # The fake answers a post with the URL, so empty stdout means
                # nothing was posted.
                self.assertEqual((result.returncode, result.stdout), (3, ""))
                self.assertIn(said, result.stderr)


GOOD_TARGET = """\
---
name: fixture
description: front matter is not a heading
---
# Foo, bar (baz)
## The `make indent` target/lane
## Repeat
## Repeat
## Closing hashes ##
   ## Indented heading
Setext heading
--------------
## See [the guide](guide.md)
## Émigré café
## a_b & c—d
## The `<id>` and `__init__` hooks
````md
```
# inside four-backtick fence
```
````
~~~
# inside tilde fence
~~~
"""

RESOLVING = """\
# Fixture

[a](target.md#foo-bar-baz) [b](target.md#the-make-indent-targetlane)
[c](target.md#repeat-1) [d](target.md#closing-hashes)
[e](target.md#indented-heading) [f](target.md#setext-heading)
[g](target.md#see-the-guide) [h](target.md#émigré-café) [i](target.md#a_b--cd)
[j](target.md "Title") [k](../../../src/main.rs#L10) [l](https://example.com/x)
[self](#fixture) `[in code](nowhere.md)` [r](target.md#the-id-and-__init__-hooks)
`src/main.rs:12` `src/main.rs#L5` `Cargo.toml` `TODO.md` `make fmt_all`
`make lint` `make -j4 V=1 lint` `scripts/test.sh --fixture` `gates (rust)`

```sh
./scripts/test.sh
cat src/main.rs; ls src/main.rs, src/
make hooks
[in a fence](nowhere.md)
```
"""

DANGLING = """\
# Fixture

[m](target.md#inside-four-backtick-fence) [n](target.md#inside-tilde-fence)
[o](target.md#repeat-2) [p](target.md#description-front-matter-is-not-a-heading)
[q](missing.md)

[ref]: missing-ref.md

`src/nope.rs:3` `Nope.toml` `make test2` `make indent-check`
`scripts/test.sh --fix` `gates (lane-2)` `make -k test3`

```sh
scripts/missing.sh
make no_such_target
```
"""

DANGLING_REPORT = {
    f".claude/skills/fixture/refs.md: {what}"
    for what in (
        "anchor target.md#inside-four-backtick-fence",
        "anchor target.md#inside-tilde-fence",
        "anchor target.md#repeat-2",
        "anchor target.md#description-front-matter-is-not-a-heading",
        "link missing.md",
        "link missing-ref.md",
        "path src/nope.rs",
        "path scripts/missing.sh",
        "file Nope.toml",
        "make test2",
        "make test3",
        "make indent-check",
        "make no_such_target",
        "flag scripts/test.sh --fix",
        "CI lane lane-2",
    )
}


class SkillRefsTests(unittest.TestCase):
    def check(self, refs):
        with tempfile.TemporaryDirectory() as repo:
            repo = Path(repo)
            files = {
                "Makefile": "hooks:\n\ttrue\nindent:\n\ttrue\nfmt_all:\n\ttrue\n"
                "test lint: hooks\n\ttrue\nindent-check:= yes\n",
                ".github/workflows/check.yml": "jobs:\n  gates:\n"
                "    strategy:\n      matrix:\n        include:\n"
                "          - lane: rust\n          - lane: portable\n",
                "scripts/test.sh": "#!/bin/sh\ncase $1 in --fixture) ;; esac\n",
                "src/main.rs": "fn main() {}\n",
                "Cargo.toml": "[package]\n",
                ".claude/skills/codetrial-verify/SKILL.md": "# Verify\n",
                ".claude/skills/fixture/guide.md": "# Guide\n",
                ".claude/skills/fixture/target.md": GOOD_TARGET,
                ".claude/skills/fixture/refs.md": refs,
            }
            for name, text in files.items():
                (repo / name).parent.mkdir(parents=True, exist_ok=True)
                (repo / name).write_text(text)
            script = repo / "scripts/check-skill-refs.py"
            shutil.copy(SKILL_REFS, script)
            return subprocess.run(
                [sys.executable, str(script)], cwd=repo, capture_output=True, text=True
            )

    def test_references_that_resolve_print_nothing(self):
        result = self.check(RESOLVING)
        self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)

    def test_each_dangling_reference_is_named_once(self):
        result = self.check(DANGLING)
        self.assertEqual(result.returncode, 1, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(set(lines), DANGLING_REPORT)
        self.assertEqual(len(lines), len(DANGLING_REPORT))


if __name__ == "__main__":
    unittest.main()
