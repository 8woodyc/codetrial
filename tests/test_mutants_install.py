"""Run the workflow's tool guards against cold and restored installations."""

import importlib.util
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "support", Path(__file__).with_name("support.py")
)
support = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "check.yml"
write_executable = support.write_executable


# Cargo forwards the subcommand name to its plugin. The plugin rejects a bare
# --version, which is why a healthy restored binary used to be reinstalled.
PLUGIN = """#!/bin/sh
if [ "$*" != 'mutants --version' ]; then
    exit 2
fi
printf '%s\\n' 'cargo-mutants {version}'
exit {status}
"""

FAKE_CARGO = """import os
import sys
from pathlib import Path

binary = Path(os.environ['FAKE_BIN']) / 'cargo-mutants'
args = sys.argv[1:]
if args == ['mutants', '--version']:
    os.execv(binary, [str(binary), *args])
if args != ['install', 'cargo-mutants', '--locked', '--version',
            os.environ['CARGO_MUTANTS_VERSION'], '--force']:
    sys.exit('unexpected cargo invocation: ' + repr(args))
with Path(os.environ['INSTALL_LOG']).open('a') as log:
    log.write('install\\n')
if os.environ.get('INSTALL_FAIL') == '1':
    sys.exit(1)
binary.write_text(os.environ['INSTALLED_PLUGIN'])
binary.chmod(0o755)
"""


def install_steps():
    lines = WORKFLOW.read_text().splitlines()
    steps = {}
    job = None
    for index, line in enumerate(lines):
        match = re.fullmatch(r"  ([\w-]+):", line)
        if match:
            job = match[1]
        if line != "      - name: Install cargo-mutants":
            continue
        if lines[index + 1] != "        run: |":
            raise AssertionError("cargo-mutants installation is no longer a run block")
        body = []
        for command in lines[index + 2 :]:
            if command and not command.startswith("          "):
                break
            body.append(command[10:])
        steps[job] = "\n".join(body)
    return steps


class MutantsInstallTests(unittest.TestCase):
    def setUp(self):
        self.steps = install_steps()
        self.assertEqual(set(self.steps), {"plan-mutants", "mutants"})
        match = re.search(
            r"^  CARGO_MUTANTS_VERSION: (\S+)$", WORKFLOW.read_text(), re.M
        )
        self.assertIsNotNone(match)
        self.version = match[1]

    def check_install(self, cached=None, status=0, install_fail=False):
        for job, script in self.steps.items():
            with self.subTest(job=job):
                with tempfile.TemporaryDirectory(prefix="cargo test ") as directory:
                    sandbox = Path(directory)
                    binary = sandbox / "cargo-mutants"
                    if cached is not None:
                        write_executable(
                            binary, PLUGIN.format(version=cached, status=status)
                        )
                    cargo = sandbox / "cargo"
                    fake_script = sandbox / "fake_cargo.py"
                    fake_script.write_text(FAKE_CARGO)
                    write_executable(
                        cargo,
                        f"#!/bin/sh\nexec {shlex.quote(sys.executable)} "
                        f'{shlex.quote(str(fake_script))} "$@"\n',
                    )
                    log = sandbox / "installs"
                    env = os.environ | {
                        "PATH": f"{sandbox}{os.pathsep}{os.environ['PATH']}",
                        "FAKE_BIN": str(sandbox),
                        "INSTALL_LOG": str(log),
                        "CARGO_MUTANTS_VERSION": self.version,
                        "INSTALLED_PLUGIN": PLUGIN.format(
                            version=self.version, status=0
                        ),
                        "INSTALL_FAIL": str(int(install_fail)),
                    }
                    result = subprocess.run(
                        ["bash", "-e", "-c", script],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if install_fail:
                        self.assertNotEqual(result.returncode, 0)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                    expected = 0 if cached == self.version and status == 0 else 1
                    self.assertEqual(
                        log.read_text() if log.exists() else "", "install\n" * expected
                    )
                    if not install_fail:
                        version = subprocess.run(
                            [str(cargo), "mutants", "--version"],
                            env=env,
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        self.assertEqual(version.returncode, 0, version.stderr)
                        self.assertEqual(
                            version.stdout.strip(), f"cargo-mutants {self.version}"
                        )

    def test_valid_warm_cache_is_reused(self):
        self.check_install(cached=self.version)

    def test_cold_cache_installs_the_pin(self):
        self.check_install()

    def test_mismatched_cache_is_replaced(self):
        self.check_install(cached="0.0.0")

    def test_version_prefix_is_not_a_match(self):
        self.check_install(cached=f"{self.version}.1")

    def test_unusable_cache_is_replaced(self):
        self.check_install(cached=self.version, status=1)

    def test_failed_installation_fails_the_step(self):
        self.check_install(install_fail=True)


if __name__ == "__main__":
    unittest.main()
