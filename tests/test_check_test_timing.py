#!/usr/bin/env python3
"""Contract tests for scripts/check-test-timing.mjs, the browser-test timing gate."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "check-test-timing.mjs"


def check(source, name="a.test.js"):
    """The gate run over a directory holding one test file with `source`."""
    with tempfile.TemporaryDirectory() as scratch:
        (Path(scratch) / name).write_text(source)
        result = subprocess.run(
            ["node", str(CHECK), scratch],
            capture_output=True,
            text=True,
            check=False,
        )
    return result.returncode, result.stderr


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class CheckTestTimingTests(unittest.TestCase):
    def assertRejected(self, source, expected):
        code, message = check(source)
        self.assertEqual(code, 1, message)
        self.assertIn(expected, message)

    def assertAccepted(self, source, name="a.test.js"):
        code, message = check(source, name)
        self.assertEqual(code, 0, message)

    def test_clean_directory_passes(self):
        self.assertAccepted("await until(() => ready, { what: 'ready' });\n")

    def test_deadline_variable_is_rejected(self):
        self.assertRejected("const deadline = Date.now() + 5000;\n", "a.test.js:1")

    def test_other_names_for_a_bound_are_rejected(self):
        for name in ("limit", "giveUpAt", "expiresAt", "until"):
            with self.subTest(name=name):
                self.assertRejected(
                    f"const {name} = Date.now() + 9000;\n", "wall-clock"
                )

    def test_a_timestamp_that_is_data_is_not_a_bound(self):
        self.assertAccepted("lifecycle.endsAt = Date.now() + 45 * 60 * 1000;\n")

    def test_uncommented_wait_for_timeout_is_rejected(self):
        self.assertRejected("await page.waitForTimeout(500);\n", "fixed sleep")

    def test_uncommented_set_timeout_sleep_is_rejected(self):
        self.assertRejected(
            "await new Promise((resolve) => setTimeout(resolve, 1100));\n",
            "fixed sleep",
        )

    def test_an_arrow_resolving_sleep_is_rejected(self):
        self.assertRejected(
            "await new Promise((resolve) => setTimeout(() => resolve(), 500));\n",
            "fixed sleep",
        )

    def test_short_timer_yield_is_not_a_sleep(self):
        self.assertAccepted("await new Promise((r) => setTimeout(r, 0));\n")

    def test_comment_above_a_sleep_accepts_it(self):
        self.assertAccepted(
            "// Absence proof: outwait the debounce.\nawait page.waitForTimeout(500);\n"
        )

    def test_url_in_a_string_is_not_a_comment(self):
        self.assertRejected(
            'await fetch("http://x");\nawait page.waitForTimeout(500);\n',
            "fixed sleep",
        )

    def test_timing_ok_marker_accepts_a_deliberate_bound(self):
        self.assertAccepted(
            "// timing-ok: budget for a cold model load\n"
            "const deadline = Date.now() + budget;\n"
        )

    def test_a_bound_split_over_lines_is_rejected(self):
        self.assertRejected("const deadline =\n  Date.now() + 5000;\n", "a.test.js:1")

    def test_a_sleep_split_over_lines_with_a_trailing_comma_is_rejected(self):
        self.assertRejected(
            "await new Promise((resolve) => setTimeout(\n  resolve,\n  1000,\n));\n",
            "fixed sleep",
        )

    def test_quoted_text_is_not_code(self):
        self.assertAccepted('const note = "const deadline = Date.now() + 5000";\n')

    def test_a_marker_inside_a_string_exempts_nothing(self):
        self.assertRejected(
            'const note = "timing-ok:";\nconst deadline = Date.now() + 5000;\n',
            "wall-clock",
        )

    def test_an_apostrophe_in_a_comment_does_not_hide_later_code(self):
        self.assertRejected(
            "// it's a note\nconst deadline = Date.now() + 5000;\n", "a.test.js:2"
        )

    def test_wait_helper_is_exempt(self):
        self.assertAccepted("const deadline = Date.now() + timeout;\n", "wait.js")


if __name__ == "__main__":
    unittest.main()
