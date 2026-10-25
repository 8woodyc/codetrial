"""Run the workflow's cargo-mutants install against a stubbed release and cargo."""

import importlib.util
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "support", Path(__file__).with_name("support.py")
)
support = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)

WORKFLOW = support.WORKFLOW
write_executable = support.write_executable
ACTION = support.ROOT / ".github/actions/install-mutants/action.yml"

# CI's release installer, replaced so a case never reaches the network or
# /usr/local/bin. It records its arguments and either succeeds or fails.
FAKE_RELEASE = """#!/bin/sh
printf '%s\\n' "$*" >> "$INSTALL_LOG.release"
[ "$RELEASE_FAIL" != 1 ]
"""

FAKE_CARGO = """#!/bin/sh
printf '%s\\n' "$*" >> "$INSTALL_LOG.cargo"
[ "$CARGO_FAIL" != 1 ]
"""


class MutantsInstallTests(unittest.TestCase):
    def run_install(self, *, release_fail=False, cargo_fail=False):
        script = support.run_block(ACTION.read_text())
        with tempfile.TemporaryDirectory(prefix="cargo test ") as directory:
            sandbox = Path(directory)
            (sandbox / "scripts").mkdir()
            write_executable(
                sandbox / "scripts/install-release-binary.sh", FAKE_RELEASE
            )
            write_executable(sandbox / "cargo", FAKE_CARGO)
            log = sandbox / "log"
            env = os.environ | {
                "PATH": f"{sandbox}{os.pathsep}{os.environ['PATH']}",
                "INSTALL_LOG": str(log),
                "CARGO_MUTANTS_VERSION": self.version,
                "RELEASE_FAIL": str(int(release_fail)),
                "CARGO_FAIL": str(int(cargo_fail)),
            }
            result = subprocess.run(
                ["bash", "-e", "-c", script],
                env=env,
                cwd=sandbox,
                capture_output=True,
                text=True,
                timeout=10,
            )

            def calls(kind):
                path = Path(f"{log}.{kind}")
                return path.read_text().split() if path.exists() else []

            return result, calls("release"), calls("cargo")

    @property
    def version(self):
        match = re.search(
            r"^  CARGO_MUTANTS_VERSION: (\S+)$", WORKFLOW.read_text(), re.M
        )
        self.assertIsNotNone(match)
        return match[1]

    def test_both_jobs_use_the_shared_install(self):
        workflow = WORKFLOW.read_text()
        for name in ("plan-mutants", "mutants"):
            with self.subTest(job=name):
                step = support.step(
                    support.job(name, workflow), "Install cargo-mutants"
                )
                self.assertIn("uses: ./.github/actions/install-mutants", step)

    def test_the_release_binary_is_the_pin(self):
        result, release, cargo = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            release,
            [
                "sourcefrog/cargo-mutants",
                f"v{self.version}",
                "cargo-mutants-x86_64-unknown-linux-gnu.tar.gz",
                "cargo-mutants",
            ],
        )
        self.assertEqual(cargo, [])

    def test_missing_release_falls_back_to_a_source_install(self):
        result, _, cargo = self.run_install(release_fail=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            cargo,
            [
                "install",
                "cargo-mutants",
                "--locked",
                "--version",
                self.version,
                "--force",
            ],
        )

    def test_failed_installation_fails_the_step(self):
        result, _, _ = self.run_install(release_fail=True, cargo_fail=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
