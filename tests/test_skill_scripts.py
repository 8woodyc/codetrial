#!/usr/bin/env python3
"""Contract tests for the scripts the agent skills under .claude/skills run.

The sweeps read GitHub through `gh`, so each case serves them one GraphQL page
from a fake `gh` and asserts on the lists they print.
"""

import importlib.util
import json
import os
import shutil
import subprocess
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

# The viewer and the one account with push access. The sweeps ask `gh` for
# both before the GraphQL page; the runs listing is empty, so no PR is held.
FAKE_GH = """#!/bin/sh
case "$*" in
  "api user --jq .login") echo viewer ;;
  *collaborators*) printf 'maint\\nviewer\\n' ;;
  *actions/runs*) : ;;
  "api graphql"*) cat "$FAKE_PAGE" ;;
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
        "commits": {
            "nodes": [
                {
                    "commit": {
                        "committedDate": ago(10),
                        "checkSuites": {"nodes": [{"createdAt": ago(10)}]},
                    }
                }
            ]
        },
        "reviews": listed(),
        "comments": listed(),
        "reviewThreads": listed(),
        "timelineItems": {"nodes": []},
    }
    return node | fields


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


@unittest.skipUnless(shutil.which("jq"), "jq is not installed")
class SweepCase(unittest.TestCase):
    def sweep(self, script, page):
        with tempfile.TemporaryDirectory() as scratch:
            scratch = Path(scratch)
            (scratch / "bin").mkdir()
            support.write_executable(scratch / "bin" / "gh", FAKE_GH)
            (scratch / "page.json").write_text(json.dumps({"data": page}))
            result = subprocess.run(
                ["sh", str(script), "sysprog21/codetrial"],
                capture_output=True,
                text=True,
                env=os.environ
                | {
                    "PATH": f"{scratch / 'bin'}:{os.environ['PATH']}",
                    "FAKE_PAGE": str(scratch / "page.json"),
                },
            )
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

    def test_more_body_edits_than_read_marks_the_issue_truncated(self):
        edits = {"totalCount": 21, "nodes": []}
        swept = self.run_issues(issue(1, "Body" + FILLER, userContentEdits=edits))
        self.assertEqual(swept["truncated"], [1])


class PrSweepTests(SweepCase):
    def run_pulls(self, *pulls):
        page = {
            "repository": {
                "defaultBranchRef": {"name": "main"},
                "pullRequests": {
                    "pageInfo": {"hasNextPage": False},
                    "nodes": list(pulls),
                },
            }
        }
        return self.sweep(PR_SWEEP, page)

    def test_pr_with_no_commits_does_not_end_the_sweep(self):
        swept = self.run_pulls(pull(1, commits={"nodes": []}), pull(2))
        self.assertEqual([entry["number"] for entry in swept["prs"]], [1, 2])

    def test_conflicting_pr_without_a_request_is_asked_to_rebase(self):
        swept = self.run_pulls(pull(1, mergeable="CONFLICTING"))
        self.assertEqual(swept["rebase"], [{"pr": 1, "author": "contributor"}])

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


if __name__ == "__main__":
    unittest.main()
