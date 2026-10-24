"""The audio probe must fail closed on empty, invalid and lossy runs."""

import importlib.util
import pathlib
import unittest

SPEC = importlib.util.spec_from_file_location(
    "audio_latency_report",
    pathlib.Path(__file__).resolve().parents[1] / "scripts/audio-latency-report.py",
)
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


def run(values, queue=100, warmup=999):
    return dict(
        version=3,
        mode="local",
        queue_ms=queue,
        sample_rate=24000,
        frame_ms=10,
        burst_ms=100,
        delivery_window_ms=1000,
        requested_pulses=len(values),
        error=None,
        teardown_complete=True,
        teardown_errors=[],
        samples=[
            dict(
                sequence=i,
                warmup=i == 0,
                sent_ms=i * 1000,
                capture_ms=1,
                delivery_ms=value,
                onsets=0 if value is None else 1,
                late_onsets=0,
            )
            for i, value in enumerate([warmup, *values])
        ],
    )


class AudioLatencyReportTests(unittest.TestCase):
    def test_interpolation_and_warmup_exclusion(self):
        row = REPORT.summarize([run([10, 20, 30, 40])])[0]
        self.assertEqual(row["delivery_p50_ms"], 25)
        self.assertEqual(row["delivery_p95_ms"], 38.5)
        self.assertEqual(row["delivery_max_ms"], 40)
        self.assertEqual(row["attempted"], 4)
        self.assertTrue(row["small_n"])

    def test_loss_remains_in_denominator_and_groups_stay_separate(self):
        rows = REPORT.summarize([run([10, None]), run([20]), run([5], queue=20)])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["runs"], 2)
        self.assertEqual(rows[1]["attempted"], 3)
        self.assertEqual(rows[1]["missing"], 1)
        self.assertEqual(rows[1]["delivery_mean_ms"], 15)

    def test_all_missing_has_no_latency(self):
        row = REPORT.summarize([run([None])])[0]
        self.assertIsNone(row["delivery_p95_ms"])
        self.assertEqual(row["missing"], 1)

    def test_refuse_invalid_runs(self):
        for runs in (
            [],
            [run([])],
            [run([float("nan")])],
            [run([-1])],
            [run([True])],
            [run([1], queue=7)],
            [run([1], queue=0)],
        ):
            with self.subTest(runs=runs), self.assertRaises(ValueError):
                REPORT.summarize(runs)

    def test_refuse_ambiguous_success_and_missing_warmup(self):
        invalid = run([10])
        invalid["samples"][1]["onsets"] = 2
        with self.assertRaises(ValueError):
            REPORT.summarize([invalid])
        invalid = run([10])
        invalid["samples"][0]["warmup"] = False
        with self.assertRaises(ValueError):
            REPORT.summarize([invalid])
        invalid = run([10])
        invalid["samples"][1]["late_onsets"] = 1
        with self.assertRaises(ValueError):
            REPORT.summarize([invalid])

    def test_late_onset_counts_as_missing(self):
        late = run([10, None])
        late["samples"][2]["late_onsets"] = 1
        row = REPORT.summarize([late])[0]
        self.assertEqual(row["missing"], 1)
        self.assertEqual(row["late"], 1)

    def test_refuse_a_missing_trial(self):
        runs = [run([10]), run([20])]
        self.assertEqual(len(REPORT.summarize(runs, expected_runs=2)), 1)
        with self.assertRaisesRegex(ValueError, "expected 3 runs, got 2"):
            REPORT.summarize(runs, expected_runs=3)

    def test_refuse_duplicate_and_interrupted_runs(self):
        with self.assertRaises(ValueError):
            REPORT.summarize([run([10]), run([10])])
        interrupted = run([10])
        interrupted["error"] = "capture timed out"
        with self.assertRaises(ValueError):
            REPORT.summarize([interrupted])

    def test_refuse_failed_or_unfinished_teardown(self):
        for changes in (
            {"teardown_complete": False},
            {"teardown_errors": ["close failed"]},
            {"teardown_errors": None},
        ):
            invalid = dict(run([10]), **changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                REPORT.summarize([invalid])

    def test_refuse_impossible_delivery_windows_and_timestamps(self):
        for value in (1000, 1100, 10**400):
            with self.subTest(value=value), self.assertRaises(ValueError):
                REPORT.summarize([run([value])])
        # Without a warmup delivery the overlap check has nothing to compare,
        # so only the send order can refuse this.
        invalid = run([10], warmup=None)
        invalid["samples"][1]["sent_ms"] = 0
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            REPORT.summarize([invalid])
        invalid = run([10])
        invalid["samples"][1]["sent_ms"] = 500
        with self.assertRaises(ValueError):
            REPORT.summarize([invalid])

    def test_refuse_missing_lifecycle_and_measurement_fields(self):
        for field in ("error", "teardown_complete", "teardown_errors"):
            invalid = run([10])
            del invalid[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                REPORT.summarize([invalid])
        invalid = run([None])
        del invalid["samples"][1]["delivery_ms"]
        with self.assertRaises(ValueError):
            REPORT.summarize([invalid])


class AudioLatencyCommandTests(unittest.TestCase):
    def invoke(self, values, *args, warmup=999, changes=None):
        import json
        import subprocess
        import sys
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "run.json"
            payload = run(values, warmup=warmup)
            payload.update(changes or {})
            path.write_text(json.dumps(payload), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SPEC.origin), str(path), *args],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_latency_and_missing_budgets_control_exit_status(self):
        self.assertEqual(self.invoke([10, 20], "--max-p95-ms", "21").returncode, 0)
        self.assertEqual(self.invoke([10, 20], "--max-p95-ms", "15").returncode, 1)
        self.assertEqual(self.invoke([10, None]).returncode, 1)
        self.assertEqual(
            self.invoke([10, None], "--max-missing-rate", "0.5").returncode, 0
        )
        self.assertEqual(self.invoke([None], "--max-missing-rate", "1").returncode, 1)

    def test_saved_teardown_failure_or_pending_status_cannot_pass(self):
        for changes in (
            {"teardown_errors": ["unpublish failed"]},
            {"teardown_complete": False},
        ):
            result = self.invoke([10], changes=changes)
            self.assertEqual(result.returncode, 2)
            self.assertIn("teardown", result.stderr)

    def test_budget_compares_unrounded_latency(self):
        self.assertEqual(self.invoke([50.0004], "--max-p95-ms", "50").returncode, 1)
        result = self.invoke([49.9996], "--max-p95-ms", "49.9998", "--format", "json")
        self.assertEqual(result.returncode, 0)

    def test_missing_warmup_fails_any_budget(self):
        # A delivery slower than the window aliases into the next pulse, so
        # only the warmup shows it.
        result = self.invoke([100], "--max-missing-rate", "1", warmup=None)
        self.assertEqual(result.returncode, 1)

    def test_machine_readable_outputs(self):
        import csv
        import io
        import json

        result = self.invoke([12], "--format", "json")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)[0]["delivery_p95_ms"], 12)
        result = self.invoke([12], "--format", "csv")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            list(csv.DictReader(io.StringIO(result.stdout)))[0]["delivered"], "1"
        )

    def test_invalid_budget_refused(self):
        self.assertEqual(self.invoke([12], "--max-p95-ms", "nan").returncode, 2)
        self.assertEqual(self.invoke([12], "--max-missing-rate", "-1").returncode, 2)


class AudioLatencyRunnerTests(unittest.TestCase):
    def test_alternating_trials_provenance_and_incomplete_run(self):
        import contextlib
        import csv
        import io
        import json
        import os
        import subprocess
        import sys
        import tempfile
        from unittest import mock

        runner_spec = importlib.util.spec_from_file_location(
            "measure_audio_latency",
            pathlib.Path(SPEC.origin).with_name("measure-audio-latency.py"),
        )
        runner = importlib.util.module_from_spec(runner_spec)
        runner_spec.loader.exec_module(runner)
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            # A configured target moves the executable; Cargo's report of
            # the build is what the runner follows.
            binary = root / "x86_64-unknown-linux-gnu/debug/examples/audio-latency"
            stale = root / "debug/examples/audio-latency"
            stale.parent.mkdir(parents=True)
            stale.write_bytes(b"stale executable")
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"fixture executable")
            order = []
            drop_last = False
            build_fails = False
            teardown_fails = False
            refused = False
            silent = False
            errors = io.StringIO()

            def command(args, **kwargs):
                if args[:2] == ["cargo", "build"] and build_fails:
                    raise subprocess.CalledProcessError(101, args)
                if args[:2] == ["cargo", "build"]:
                    output = "\n".join(
                        json.dumps(message)
                        for message in (
                            dict(reason="compiler-artifact", target=dict(name="dep")),
                            dict(
                                reason="compiler-artifact",
                                target=dict(name="audio-latency"),
                                executable=str(binary),
                            ),
                            dict(reason="build-finished", success=True),
                        )
                    )
                elif args[0] == "rustc":
                    output = "rustc fixture"
                elif args[:2] == ["git", "rev-parse"]:
                    output = "fixture revision"
                elif args[0] == str(stale):
                    self.fail("the runner ran an executable Cargo did not build")
                elif args[0] == str(binary):
                    order.append(int(args[2]))
                    if silent:
                        raise subprocess.CalledProcessError(1, args)
                    if drop_last and len(order) == 4:
                        raise subprocess.CalledProcessError(1, args)
                    # The probe runs from the repository root.
                    self.assertTrue(pathlib.Path(args[4]).is_absolute())
                    trial = run([10, 20 + len(order)], queue=int(args[2]))
                    if refused and len(order) == 4:
                        trial["teardown_errors"] = ["close failed"]
                    pathlib.Path(args[4]).write_text(
                        json.dumps(trial), encoding="utf-8"
                    )
                    if teardown_fails and len(order) == 4:
                        raise subprocess.CalledProcessError(1, args)
                    output = ""
                else:
                    output = ""
                return subprocess.CompletedProcess(args, 0, stdout=output)

            def invoke(output):
                argv = [
                    "measure-audio-latency.py",
                    "--output",
                    str(output),
                    "--iterations",
                    "2",
                    "--pulses",
                    "2",
                ]
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(sys, "argv", argv))
                    stack.enter_context(
                        mock.patch.dict(os.environ, {"CXX": "fixture-clang"})
                    )
                    stack.enter_context(
                        mock.patch.object(runner, "command", side_effect=command)
                    )
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    errors.seek(0)
                    errors.truncate()
                    stack.enter_context(contextlib.redirect_stderr(errors))
                    return runner.main()

            output = root / "complete"
            self.assertEqual(invoke(output), 0)
            self.assertEqual(order, [100, 20, 20, 100])
            self.assertEqual(len(list(output.glob("iter-*.json"))), 4)
            self.assertTrue((output / "summary.csv").exists())
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["revision"], "fixture revision")
            self.assertEqual(len(manifest["binary_sha256"]), 64)
            self.assertEqual(len(manifest["source_sha256"]), 64)
            order.clear()
            drop_last = True
            self.assertEqual(invoke(root / "incomplete"), 1)
            self.assertEqual(len(list((root / "incomplete").glob("iter-*.json"))), 3)
            # The three surviving trials must not read as a healthy experiment.
            self.assertIn(
                "expected 4 runs, got 3",
                json.loads((root / "incomplete/summary.json").read_text())["error"],
            )

            order.clear()
            drop_last = False
            previous = os.getcwd()
            os.chdir(root)
            try:
                self.assertEqual(invoke(pathlib.Path("relative")), 0)
            finally:
                os.chdir(previous)
            self.assertTrue((root / "relative/summary.json").exists())

            order.clear()
            teardown_fails = True
            self.assertEqual(invoke(root / "teardown-failed"), 1)
            self.assertEqual(
                len(list((root / "teardown-failed").glob("iter-*.json"))), 4
            )
            teardown_fails = False

            order.clear()
            refused = True
            self.assertEqual(invoke(root / "refused"), 1)
            refused_dir = root / "refused"
            self.assertIn("teardown", (refused_dir / "summary.text").read_text())
            self.assertIn(
                "teardown",
                json.loads((refused_dir / "summary.json").read_text())["error"],
            )
            with (refused_dir / "summary.csv").open(encoding="utf-8") as rows:
                self.assertIn("teardown", next(csv.DictReader(rows))["error"])
            refused = False

            # No trial left a result: the summaries still carry the refusal,
            # and it is reported once rather than once per format.
            order.clear()
            silent = True
            self.assertEqual(invoke(root / "silent"), 1)
            self.assertIn(
                "expected 4 runs, got 0",
                json.loads((root / "silent/summary.json").read_text())["error"],
            )
            self.assertIn("got 0", (root / "silent/summary.text").read_text())
            self.assertEqual(errors.getvalue().count("got 0"), 1)
            silent = False

            build_fails = True
            with self.assertRaises(subprocess.CalledProcessError):
                invoke(root / "unbuilt")
            self.assertFalse((root / "unbuilt").exists(), "a rerun may reuse the name")
