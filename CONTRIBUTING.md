# Contributing to CodeTrial

[README.md](README.md) covers what CodeTrial is, how to build it, and how to run
it. [docs/development.md](docs/development.md) covers the test gate, the
formatter chain, the git hooks, and the generated files. This page covers what
neither does: how a change gets in, and the rules a reviewer would otherwise
have to say out loud every time.

Everything posted to GitHub is written in clear English, with identifiers and
error messages kept verbatim. The conversation that produced a change can be in
any language; the issue, the pull request, and the code cannot.

## Reporting a bug

Use the [bug report form](https://github.com/sysprog21/codetrial/issues/new/choose).
It asks for the facts a maintainer always needs, because asking for them
afterwards costs a round trip that usually never completes.

Before filing, search
[open and closed issues](https://github.com/sysprog21/codetrial/issues?q=is%3Aissue+sort%3Aupdated-desc).
If a thread already covers the symptom, add the new evidence there instead of
opening a second one.

A feature idea, a question, or a design objection goes in a blank issue, which
the chooser offers beside the form. Only bug reports get a form, because only
bug reports have a fixed set of facts a maintainer always needs.

Nothing public carries credentials, session tokens, candidate recordings, or
personal interview content. Sanitize logs and screenshots before attaching
them. This repository has no security mailbox and GitHub's private
vulnerability reporting is turned off, so there is no private channel to send a
report to. Do not put the details anywhere public: open an issue that says only
that you have a security report and asks for a private contact, and wait for
one.

## Before changing code

`docs/` holds decisions that are already made, and several of them refuse a
change that looks obviously right. Find the document that owns the area and
read the rule there first:
[provider cost and degradation](docs/provider-cost-and-degradation.md) owns what
an interview may spend and what may be cached, the
[observable delivery policy](docs/observable-delivery-policy.md) owns what a
report may say about a person, the
[recording contract](docs/recording-contract.md) owns what happens to candidate
media, and the rest of `docs/` owns its area the same way.

A document that refuses your change is the answer, not an oversight. The move
is to propose changing that document on purpose, with the reason, rather than
working around it.

A subcommand, flag, or helper that turns out to be redundant is deleted, along
with its call sites in the Makefile, the scripts, the README, and the tests. It
does not become an alias and it does not get a deprecation warning. The
compatibility promise here is the versioned contract in
[docs/interview-contract-versions.md](docs/interview-contract-versions.md), not
the convenience surface of the CLI. The prompt and report goldens in
`tests/golden/` are how a change to that contract gets noticed, not a promise of
their own: they are regenerated on purpose when a version is bumped. The other
fixtures there answer to the tests that own them.

## Tests

Every non-trivial change leaves a runnable check behind: the smallest thing
that fails if the change breaks.

`src/` holds implementation and must not contain a test body. A module declares
its tests and the body lives under `tests/unit/`, mirroring the path:

```rust
#[cfg(test)]
#[path = "../tests/unit/gemini.rs"]
mod tests;
```

Declared this way they are still unit tests, with `super::` and private items
in reach. A standalone integration target under `tests/` compiles as its own
crate and reaches only the public API, which would have meant publishing
internals for the tests' benefit. Never name such a file `tests/<dir>/main.rs`:
Cargo compiles that as a second crate root, where every `super::` in it fails.

No gate enforces the rule, so `grep -rn '#\[test\]' src/` is the check, and it
returns nothing today.

## Running the gate

```bash
make hooks           # once per clone; installs every scripts/git-*.sh hook
./scripts/test.sh    # the credential-free gate CI runs
```

`make hooks` installs a wrapper for each `scripts/git-*.sh`, so the push is
gated as well as the commit and a rebase that rewrote a message is caught before
it reaches GitHub. It exits 0 only when every hook is in effect; a hook of your
own already in the way, or a `core.hooksPath` shared with other repositories, is
left alone and reported with how to integrate it. Your own hook keeps running
once moved to `.git/hooks/<name>.local`, after ours.
`./scripts/install-git-hooks.sh --check` asks the same question without writing.
`cargo build` runs the same installer whenever that check fails, and skips it
once the hooks are in effect; what needs you shows up as a Cargo warning.

The gate ends by naming any lane it skipped; read that rather than assuming
green means covered. [docs/development.md](docs/development.md) has
what each hook and lane does, which generated files have to be regenerated
first, and the checks that need credentials or a browser.

## Commit messages

The house style is Chris Beams' seven rules. `scripts/git-commit-msg.sh` both
prints the enforced list and enforces it, so read it from there rather than from
a copy that can drift:

```bash
./scripts/git-commit-msg.sh --rules
```

The `prepare-commit-msg` hook puts the same list in your editor. Subjects from
this tree's log read like this:

```
Bound a video frame by the memory it will take
Refuse a permission listing that may be truncated
Prove an empty test run is not a pass
```

A commit needs a body: the hook and CI refuse a subject alone. Fixup, squash and
merge commits are exempt, and so are Dependabot's. The body carries the premise
and the trade, usually in a single paragraph. This tree keeps its detailed
reasoning in the comment next to the code, so a body retelling the mechanics
duplicates that comment and then goes stale on its own.
The hook rejects the shape that does it outright: a body line opening with
`How:`, `Step:`, `Steps:`, `Changes:` or an `Implementation:` heading. Whether
what is left says anything is the part only a reviewer can judge.

Comments follow from the same rule: every line says something the code cannot.
The ASCII rule the hook enforces covers the commit message, and source comments
follow it by convention, with no em dash, typographic quote, arrow character, or
CJK.

## Pull requests

Open a pull request from a topic branch, never from `main`, including the
`main` of a fork. A pull request follows its head branch, so one opened from
`main` takes in every later push there and blocks the next change until it
merges. The pre-push hook refuses new work on a fork's `main` once upstream is
a remote in the clone, and CI fails a pull request opened from one either way.

Branch from an up-to-date `main`, keep one change per branch, and update the
pull request by pushing to that same branch, force-pushing after a rebase.
Syncing a fork's `main` with upstream is still allowed, since it carries only
commits upstream already has.

A title carries no category, type, or area prefix: no `[Bug]`, `[Feature]`,
`Bug:`, `feat:`, `fix(web):`, or `web:`. GitHub issue types do the classifying,
and brackets that are part of the text, such as `argv[0]`, stay. A pull request
title follows the commit subject rules above; a single-commit pull request
reuses its subject. An issue title names the symptom or the outcome instead:
`Interview does not resume after reconnecting`, not `[Bug] Interview broken`.

Issue and pull request bodies are read in a browser, so write each paragraph as
one line. The 72-column wrap is for commit messages only. A pull request that
finishes an issue ends with `Closes #N` alone on the last line. Avoid `Refs #N`:
a reference that closes nothing is easy to add to every pull request and tells
the reviewer little. Use it only beside a `Closes #N`, or in a body that already
states the motivation and the implementation in full, so the reference adds
context rather than standing in for it.

A review thread carries a correction, a measurement, or nothing: no pasted
agent walkthroughs, no severity tables, no re-summarizing a diff that GitHub
already shows. Close an addressed thread with "Resolve conversation".
