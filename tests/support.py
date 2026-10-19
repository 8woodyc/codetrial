"""What the Python suites share: the stubs they run and the workflow they read.

Not a test file. The suites load it by path, the way they load the scripts they
test, because `tests/` is not a package and `scripts/run-python-tests.py`
imports each suite from its own path.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/check.yml"


def write_executable(path, text):
    """Write a stub a case will run, without this process ever holding it open.

    The runner runs cases on threads, and a file one thread has open for writing
    stays open in any process another thread forks until that process execs.
    Running the stub in that window fails with "Text file busy". `cp` writes it
    from a process of its own.
    """
    source = path.with_name(path.name + ".source")
    source.write_text(text)
    subprocess.run(["cp", str(source), str(path)], check=True)
    path.chmod(0o755)


# The workflow is read as text, because the gate's Python has no YAML library.
# Each helper names what it could not find, so a reshaped workflow fails with
# the job or step it lost rather than an IndexError.
def section(text, start, end, what):
    if start not in text:
        raise AssertionError(f"{what} is no longer in the workflow")
    return text.split(start, 1)[1].split(end, 1)[0]


def job(name, text=None):
    """The body of one job, up to the next job."""
    text = WORKFLOW.read_text() if text is None else text
    body = section(text, f"\n  {name}:\n", "\0", f"job {name}")
    following = re.search(r"\n  [a-z][a-z0-9_-]*:\n", body)
    return body[: following.start()] if following else body


def step(text, name):
    return section(text, f"      - name: {name}\n", "\n      - ", f"step {name}")


def run_block(text):
    """The script of the first `run: |` in text, dedented."""
    body = []
    for line in section(text, "        run: |\n", "\0", "run block").splitlines():
        if line and not line.startswith("          "):
            break
        body.append(line[10:])
    return "\n".join(body)
