#!/usr/bin/env python3
"""Contract tests for scripts/browser-stress.sh, the loaded-runner repeat harness."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRESS = ROOT / "scripts" / "browser-stress.sh"

PASSING = 'import { test } from "node:test";\ntest("passes", () => {});\n'
FAILING = (
    'import { test } from "node:test";\n'
    'import assert from "node:assert";\n'
    'test("fails", () => assert.equal(1, 2));\n'
)


def stress(*args, sources=()):
    """The script run over one test file per source, all named case.test.mjs."""
    with tempfile.TemporaryDirectory() as scratch:
        files = []
        for index, source in enumerate(sources):
            directory = Path(scratch) / str(index)
            directory.mkdir()
            (directory / "case.test.mjs").write_text(source)
            files.append(str(directory / "case.test.mjs"))
        # A failing run keeps its log under TMPDIR; keep that in the scratch.
        return subprocess.run(
            ["sh", str(STRESS), *args, *files],
            env={**os.environ, "TMPDIR": scratch},
            capture_output=True,
            text=True,
            check=False,
        )


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class BrowserStressTests(unittest.TestCase):
    def test_passing_file_exits_zero(self):
        result = stress("-n", "2", "-j", "1", sources=[PASSING])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0 of 2 runs failed", result.stdout)

    def test_failing_file_exits_one_and_names_its_output(self):
        result = stress("-n", "2", "-j", "1", sources=[FAILING])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("2 of 2 runs failed", result.stdout)
        self.assertIn("output in", result.stdout)

    def test_a_count_that_would_run_nothing_is_refused(self):
        for flag, value in (
            ("-n", "0"),
            ("-n", "00"),
            ("-n", "abc"),
            ("-n", "99999999999999999999"),
            ("-j", "0"),
            ("-j", "x"),
        ):
            with self.subTest(flag=flag, value=value):
                result = stress(flag, value, sources=[PASSING])
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn("positive integer", result.stderr)

    def test_two_files_with_one_name_keep_separate_logs(self):
        result = stress("-n", "1", "-j", "1", sources=[FAILING, FAILING])
        logs = [
            line.split("output in ")[1]
            for line in result.stdout.splitlines()
            if "output in" in line
        ]
        self.assertEqual(len(logs), 2, result.stdout)
        self.assertEqual(len(set(logs)), 2, "the second failure overwrote the first")

    def test_no_files_prints_usage(self):
        result = stress("-n", "1")
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)


if __name__ == "__main__":
    unittest.main()
