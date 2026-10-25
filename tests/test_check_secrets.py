"""Use real Git objects to verify offline secret checks and redacted failures."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

# Split, so the hooks that run this checker do not refuse this file.
BEGIN = "-----" + "BEGIN "
SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check-secrets.py"


class SecretChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.org")
        self.git("config", "user.name", "Fixture")
        self.git("config", "core.hooksPath", os.devnull)
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args):
        return (
            subprocess.run(
                ["git", *args],
                cwd=self.root,
                env=self.env,
                capture_output=True,
                check=True,
            )
            .stdout.decode()
            .strip()
        )

    def write(self, name, text, stage=True):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if stage:
            self.git("add", "--", name)

    def scan(self, *args, commits="", env=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.root,
            env=env or self.env,
            input=commits,
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_clean(self, result=None):
        result = result or self.scan()
        self.assertEqual(result.returncode, 0, result.stderr)

    def diverge(self, side_text):
        """Commit base, then a side branch and a main commit; return the main branch."""
        self.write("base.txt", "safe")
        self.git("commit", "-qm", "base")
        branch = self.git("symbolic-ref", "--short", "HEAD")
        self.git("checkout", "-qb", "side")
        self.write("side.txt", side_text)
        self.git("commit", "-qm", "side")
        self.git("checkout", branch)
        self.write("main.txt", "safe")
        self.git("commit", "-qm", "main")
        return branch

    def assert_blocked(self, result, secret):
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("redacted", result.stderr)
        self.assertNotIn(secret, result.stderr + result.stdout)

    def test_staged_secret_is_blocked_even_if_worktree_is_cleaned(self):
        key = "AIza" + "a1B_" * 8 + "xyz"
        self.write("settings.txt", key)
        self.write("settings.txt", "removed\n", stage=False)
        self.assert_blocked(self.scan(), key)

    def test_unstaged_secret_does_not_block_clean_index(self):
        self.write("settings.txt", "safe\n")
        self.write("settings.txt", "ghp_" + "x" * 36, stage=False)
        self.assert_clean()

    def test_local_literal_is_detected_without_executing_env(self):
        secret = "fixture-literal-credential-8427"
        self.write(
            ".env.local",
            'API_TOKEN="' + secret + '" # local token\nOTHER=$(touch marker)\n',
            stage=False,
        )
        self.write("data.txt", secret)
        self.assert_blocked(self.scan(), secret)
        self.assertFalse((self.root / "marker").exists())

    def test_templates_are_allowed_but_live_env_paths_are_not(self):
        self.write("config/.env.example", "KEY=YOUR_KEY\n")
        self.assert_clean()
        self.write("config/.env.production", "SAFE=1\n")
        result = self.scan()
        self.assertEqual(result.returncode, 1)
        self.assertIn("local environment file", result.stderr)

    def test_binary_and_odd_paths_are_not_exempt(self):
        name = "odd\nname.bin"
        key = "ghp_" + "z" * 36
        (self.root / name).write_bytes(b"\x00" + key.encode())
        self.git("add", "--", name)
        self.assert_blocked(self.scan(), key)
        self.assertIn("odd\\nname.bin", self.scan().stderr)

    def test_secret_in_filename_is_redacted(self):
        key = "ghp_" + "k" * 36
        self.write(key, "safe")
        self.assert_blocked(self.scan(), key)

    def test_deleted_secret_does_not_make_outgoing_history_clean(self):
        key = "ghp_" + "q" * 36
        self.write("data.txt", key)
        self.git("commit", "-qm", "first")
        first = self.git("rev-parse", "HEAD")
        self.write("data.txt", "safe")
        self.git("commit", "-qm", "second")
        second = self.git("rev-parse", "HEAD")
        self.assert_blocked(
            self.scan("--commits", commits=second + "\n" + first + "\n"), key
        )

    def test_alternate_index_is_used(self):
        key = "ghp_" + "w" * 36
        self.write("data.txt", key)
        alternate = self.root / ".git/alternate-index"
        alternate.write_bytes((self.root / ".git/index").read_bytes())
        self.write("data.txt", "safe")
        self.assert_clean()
        env = dict(self.env, GIT_INDEX_FILE=str(alternate))
        self.assert_blocked(self.scan(env=env), key)

    def test_symlink_contents_are_not_followed(self):
        (self.root / "link").symlink_to("/nonexistent/private-file")
        self.git("add", "link")
        self.assert_clean()

    def test_bad_revision_fails_closed_without_echoing_input(self):
        result = self.scan("--commits", commits="bad-sensitive-input")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("bad-sensitive-input", result.stderr)

    def test_submodule_objects_are_not_read_as_blobs(self):
        self.git(
            "update-index", "--add", "--cacheinfo", "160000," + "a" * 40 + ",module"
        )
        self.assert_clean()

    def test_merge_resolution_is_scanned(self):
        self.diverge("safe")
        self.git("merge", "--no-ff", "--no-commit", "side")
        key = "ghp_" + "r" * 36
        self.write("resolution.txt", key)
        self.git("commit", "-qm", "merge")
        self.assert_blocked(
            self.scan("--commits", commits=self.git("rev-parse", "HEAD")), key
        )

    def test_google_key_ending_in_dash_is_detected(self):
        key = "AIza" + "a" * 34 + "-"
        self.write("settings.txt", "key=" + key + "\n")
        self.assert_blocked(self.scan(), key)

    def test_token_after_an_underscore_is_detected(self):
        key = "ghp_" + "u" * 36
        self.write("settings.txt", "export X=foo_" + key + "\n")
        self.assert_blocked(self.scan(), key)

    def test_pgp_private_key_block_is_detected(self):
        self.write("key.asc", BEGIN + "PGP PRIVATE KEY BLOCK-----\n")
        self.assertEqual(self.scan().returncode, 1)

    def test_key_header_is_allowed_under_tests_but_not_elsewhere(self):
        header = BEGIN + "PRIVATE KEY-----\n"
        self.write("tests/unit/moved_fixture.rs", header)
        self.assert_clean()
        self.write("src/other.rs", header)
        self.assertEqual(self.scan().returncode, 1)

    def test_token_under_tests_is_still_blocked(self):
        key = "ghp_" + "t" * 36
        self.write("tests/unit/fixture.rs", key)
        self.assert_blocked(self.scan(), key)

    def test_local_value_with_dollar_is_kept_but_references_are_not(self):
        secret = "pass$word-12345"
        self.write(
            ".env.local",
            "API_PASSWORD='" + secret + "'\nAPI_TOKEN=$OTHER_TOKEN_VALUE\n",
            stage=False,
        )
        self.write("data.txt", secret)
        self.assert_blocked(self.scan(), secret)
        self.write("data.txt", "$OTHER_TOKEN_VALUE")
        self.assert_clean()

    def test_overlapping_redactions_leave_no_token_tail(self):
        tail = "k" * 32
        self.write(".env.local", "API_TOKEN=ghp_abcd\n", stage=False)
        self.write("ghp_abcd" + tail, "safe")
        result = self.scan()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(tail, result.stderr)

    def test_path_that_looks_like_a_revision_is_read_as_a_path(self):
        key = "ghp_" + "m" * 36
        self.write("1:x", key)
        self.assert_blocked(self.scan(), key)

    def test_merge_does_not_rescan_content_taken_from_a_parent(self):
        key = "ghp_" + "n" * 36
        self.diverge(key)
        self.git("merge", "--no-ff", "-m", "merge", "side")
        self.assert_clean(self.scan("--commits", commits=self.git("rev-parse", "HEAD")))

    def test_scan_preserves_index_bytes(self):
        self.write("safe.txt", "safe")
        index = self.root / ".git/index"
        before = index.read_bytes()
        self.assert_clean()
        self.assertEqual(index.read_bytes(), before)

    def test_git_failure_fails_closed(self):
        result = self.scan(env=dict(self.env, GIT_DIR=str(self.root / "missing")))
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
