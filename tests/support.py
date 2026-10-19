"""What the Python suites share: the stubs they run.

Not a test file. The suites load it by path, the way they load the scripts they
test, because `tests/` is not a package and `scripts/run-python-tests.py`
imports each suite from its own path.
"""

import subprocess


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
