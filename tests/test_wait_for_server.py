#!/usr/bin/env python3
"""Contract tests for scripts/wait-for-server.sh, the loop the check scripts share."""

import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "wait-for-server.sh"


class Home(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"home")

    def log_message(self, *args):
        pass


def wait(url, seconds, log, pid="", out=""):
    """wait_for_server run in a fresh sh, the way a check script sources it."""
    started = time.monotonic()
    result = subprocess.run(
        [
            "sh",
            "-c",
            '. "$1"; shift; wait_for_server "$@"',
            "sh",
            str(HELPER),
            url,
            str(seconds),
            str(log),
            pid,
            out,
        ],
        capture_output=True,
        text=True,
    )
    return result, time.monotonic() - started


class WaitForServerTests(unittest.TestCase):
    def setUp(self):
        sandbox = tempfile.TemporaryDirectory()
        self.addCleanup(sandbox.cleanup)
        self.tmp = Path(sandbox.name)
        self.log = self.tmp / "server.log"
        self.log.write_text("listening on nothing\n")

    def test_an_answering_server_returns_its_response(self):
        server = HTTPServer(("127.0.0.1", 0), Home)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        out = self.tmp / "home.html"
        url = f"http://127.0.0.1:{server.server_port}/"
        result, _ = wait(url, 5, self.log, out=str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(out.read_text(), "home")

    def test_a_silent_server_runs_out_the_limit_and_shows_its_log(self):
        # Port 9 (discard) is closed on a test machine, so nothing answers.
        result, elapsed = wait("http://127.0.0.1:9/", 2, self.log)
        self.assertEqual(result.returncode, 1)
        self.assertIn("did not answer within 2 seconds", result.stderr)
        self.assertIn("listening on nothing", result.stderr)
        self.assertGreaterEqual(elapsed, 2)

    def test_a_server_that_accepts_and_never_replies_still_meets_the_limit(self):
        # A closed port refuses at once and never exercises the probe timeout.
        stalled = socket.socket()
        stalled.bind(("127.0.0.1", 0))
        stalled.listen(8)
        self.addCleanup(stalled.close)
        url = f"http://127.0.0.1:{stalled.getsockname()[1]}/"
        result, elapsed = wait(url, 2, self.log)
        self.assertEqual(result.returncode, 1)
        self.assertIn("did not answer within 2 seconds", result.stderr)
        self.assertLess(elapsed, 8)

    def test_a_server_that_already_exited_fails_at_once(self):
        gone = subprocess.Popen(["true"])
        gone.wait()
        result, elapsed = wait("http://127.0.0.1:9/", 30, self.log, str(gone.pid))
        self.assertEqual(result.returncode, 1)
        self.assertIn("exited before", result.stderr)
        self.assertIn("listening on nothing", result.stderr)
        self.assertLess(elapsed, 10)


if __name__ == "__main__":
    unittest.main()
