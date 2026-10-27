#!/usr/bin/env python3
"""Start, validate, preview and build an instructor task set; never publish
it.

    task-package.py references                 tasks to start from
    task-package.py new [--from REF]           add one to the bank
    task-package.py build --site URL           encrypt the bank for a site

The bank is the directory `tasks` and the output `publish`, both in the
current directory, unless --bank and --output name others.
"""

import sys

from problem_bank import runtime

# Before anything that needs a newer Python: only `build` encrypts, and not
# when it is only asked for its help.
runtime.ensure(
    needs_cryptography=sys.argv[1:2] == ["build"]
    and not any(arg == "-h" or arg.startswith("--h") for arg in sys.argv[2:])
)

import argparse  # noqa: E402
import getpass  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import shlex  # noqa: E402
from pathlib import Path  # noqa: E402

from problem_bank.task_authoring import (  # noqa: E402
    DEFAULT_REFERENCE,
    new_task,
    next_version,
    references,
)
from problem_bank.task_package import (  # noqa: E402
    assignment_link,
    build_package,
    check_output,
    load_bank,
    preview,
    publish_scan,
    render_prompt,
    site_base,
)
from problem_bank.tasks import IDENTIFIER  # noqa: E402


def read_pin():
    """The PIN from a non-echoing prompt, asked twice, or once from stdin."""
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\r\n")
    pin = getpass.getpass("PIN: ")
    if getpass.getpass("PIN again: ") != pin:
        raise ValueError("the two PINs differ")
    return pin


# Where the tasks are and where the built set goes, unless told otherwise.
DEFAULT_BANK = Path("tasks")
DEFAULT_OUTPUT = Path("publish")


def chosen_task(package, task_id):
    """`task_id`, or the bank's only task when none is named."""
    if task_id is not None:
        return task_id
    ids = sorted(package["variants"])
    if len(ids) != 1:
        raise ValueError(
            f"the bank holds {len(ids)} tasks; pass --task-id: {', '.join(ids)}"
        )
    return ids[0]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("references", help="list the tasks `new` can start from")
    arg = sub.add_parser("new", help="add a copy of a reference task to a bank")
    arg.add_argument(
        "--bank", type=Path, default=DEFAULT_BANK, help=f"{DEFAULT_BANK} by default"
    )
    arg.add_argument(
        "--from",
        dest="reference",
        default=DEFAULT_REFERENCE,
        metavar="REF",
        help=f"the task to copy, {DEFAULT_REFERENCE} by default",
    )
    arg.add_argument("--id", help="the new task's id; the reference's by default")
    arg.add_argument(
        "--languages",
        type=lambda text: [part.strip() for part in text.split(",") if part.strip()],
        help="comma-separated, the first offered first; the reference's by"
        " default (python for a catalog problem)",
    )
    for command in ("validate", "render-prompt", "preview", "build"):
        arg = sub.add_parser(command)
        arg.add_argument(
            "--bank", type=Path, default=DEFAULT_BANK, help=f"{DEFAULT_BANK} by default"
        )
        # Authoring commands read the bank alone. A build names the set (the
        # bank's directory name unless given) and its version (the next one
        # the output does not hold unless given).
        building = command == "build"
        arg.add_argument("--set-id", default=None if building else "preview")
        arg.add_argument("--version", type=int, default=None if building else 1)
        if command in {"render-prompt", "preview"}:
            arg.add_argument("--task-id", help="the bank's only task by default")
        if command == "render-prompt":
            # One of the task's languages; the first it lists by default.
            arg.add_argument("--language")
        if building:
            arg.add_argument("--site", required=True)
            arg.add_argument(
                "--output",
                type=Path,
                default=DEFAULT_OUTPUT,
                help=f"{DEFAULT_OUTPUT} by default",
            )
    arg = sub.add_parser("publish-scan")
    arg.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "publish-scan":
            print(f"Validated {publish_scan(args.directory)} encrypted packages")
        elif args.command == "references":
            try:
                for task_id, title, kind in references():
                    print(f"{task_id:32} {kind:8} {title}")
                sys.stdout.flush()
            except BrokenPipeError:
                # Read by `head` or a pager that has seen enough.
                os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        elif args.command == "new":
            task_id, notes = new_task(
                args.bank, args.reference, args.id, args.languages
            )
            preview_command = shlex.join(
                [sys.executable, sys.argv[0], "preview"]
                + (["--bank", str(args.bank)] if args.bank != DEFAULT_BANK else [])
                + ["--task-id", task_id]
            )
            print(
                f"Added {task_id} to {args.bank}. Edit its files there"
                " (task-mode.json holds the sidecar, task-set.json the rules),"
                f" then check what the learner sees with\n  {preview_command}"
            )
            for note in notes:
                print(f"Note: {note}.")
        elif args.command == "build":
            set_id = args.set_id
            if set_id is None:
                set_id = args.bank.resolve().name
                if not IDENTIFIER.fullmatch(set_id):
                    raise ValueError(
                        f"the bank directory name {set_id!r} is not a valid set"
                        " id (a lowercase letter, then lowercase letters, digits"
                        " or dashes); pass --set-id"
                    )
            version, first = args.version, False
            if version is None:
                version, first = next_version(args.output, set_id)
            # Everything but the PIN checked first, so a mistake costs no PIN.
            site = site_base(args.site)
            package = load_bank(args.bank, set_id, version)
            check_output(args.output, set_id, version)
            print(f"Building set {set_id}, version {version}.")
            if first:
                print(
                    f"{args.output} holds no version of {set_id} yet. If you"
                    " published this set from another directory, stop and"
                    " pass --version above its last one."
                )
            directory = build_package(package, read_pin(), site, args.output)
            landing = f"{site.rstrip('/')}/sets/{set_id}/{version}/"
            print(f"Built {directory}.")
            print(
                f"Publish {args.output} as the root of {site} (for GitHub Pages,"
                " push it to a repository and turn Pages on for that branch),"
                f" then give learners the page {landing} or these links and,"
                " separately, the PIN:"
            )
            for task_id, variant in sorted(package["variants"].items()):
                link = assignment_link(site, set_id, task_id, version)
                print(f"  {variant['title']}: {link}")
        else:
            package = load_bank(args.bank, args.set_id, args.version)
            if args.command == "validate":
                print(f"Validated {len(package['problems'])} tasks")
            elif args.command == "render-prompt":
                task_id = chosen_task(package, args.task_id)
                print(render_prompt(package, task_id, args.language))
            else:
                task_id = chosen_task(package, args.task_id)
                print(json.dumps(preview(package, task_id), indent=2))
    except (
        OSError,
        ValueError,
        RuntimeError,
        ImportError,
        KeyError,
        TypeError,
    ) as error:
        print(f"task-package: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
