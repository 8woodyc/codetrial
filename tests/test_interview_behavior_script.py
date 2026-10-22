"""Keep credentialed injection probes opt-in at the shell entry point."""

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "support", Path(__file__).with_name("support.py")
)
support = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)


class BehaviorScriptTests(unittest.TestCase):
    def invoke(self, mode, status=0, cwd=None, **extra):
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            cargo = scratch / "cargo"
            support.write_executable(
                cargo,
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$MOCK_CARGO_LOG"\n'
                'printf "config=%s results=%s\\n" "${BEHAVIOR_INJECTION_CONFIG:-}" '
                '"${BEHAVIOR_INJECTION_RESULTS:-}" >> "$MOCK_CARGO_LOG.env"\n'
                'exit "${MOCK_CARGO_STATUS:-0}"\n',
            )
            log = scratch / "calls"
            env = os.environ.copy()
            env.update(
                PATH=f"{scratch}{os.pathsep}{env.get('PATH', '')}",
                MOCK_CARGO_LOG=str(log),
                MOCK_CARGO_STATUS=str(status),
            )
            for name in (
                "BEHAVIOR_CHECK_MODE",
                "BEHAVIOR_INJECTION_CONFIG",
                "BEHAVIOR_INJECTION_RESULTS",
            ):
                env.pop(name, None)
            env.update(extra)
            if mode is not None:
                env["BEHAVIOR_CHECK_MODE"] = mode
            result = subprocess.run(
                [str(ROOT / "scripts/interview-behavior-check.sh")],
                env=env,
                cwd=cwd,
                text=True,
                capture_output=True,
                check=False,
            )

            def lines(path):
                return path.read_text().splitlines() if path.exists() else []

            return result, lines(log), lines(Path(f"{log}.env"))

    def test_default_keeps_existing_probes_and_skips_injection(self):
        result, calls, _ = self.invoke(None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertIn("--skip injection::instruction_injection_probes", calls[0])
        self.assertIn(
            "a_misrecognized_turn_neither_appears_in_nor_decides_the_report", calls[1]
        )

    def test_injection_selects_only_the_exact_bounded_probe(self):
        result, calls, _ = self.invoke("injection")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)
        self.assertIn("injection::instruction_injection_probes", calls[0])
        self.assertIn("--ignored --exact --nocapture", calls[0])
        self.assertNotIn("--skip", calls[0])

    def test_failed_injection_does_not_fall_through_to_other_probes(self):
        result, calls, _ = self.invoke("injection", status=1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(calls), 1)

    def test_injection_resolves_relative_paths_from_the_caller(self):
        with tempfile.TemporaryDirectory() as directory:
            caller = Path(directory).resolve()
            result, calls, env_seen = self.invoke(
                "injection",
                cwd=caller,
                BEHAVIOR_INJECTION_CONFIG="isolated.env",
                BEHAVIOR_INJECTION_RESULTS="pilot.jsonl",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            env_seen,
            [f"config={caller}/isolated.env results={caller}/pilot.jsonl"],
        )

    def test_unknown_mode_never_calls_cargo(self):
        result, calls, _ = self.invoke("typo")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])
        self.assertIn("must be default or injection", result.stderr)


if __name__ == "__main__":
    unittest.main()
