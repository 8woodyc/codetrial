# What not to raise

Every reviewer and the verification load this file. A wrong finding costs more
than its own line: the author spends a push or a reply on it, and the next
correct one is read with less trust. The patterns below are where findings
here have turned out wrong. The first list comes from the replies to cubic's
comments on this repository, where the comments a human pushed back on were
nearly all one of these kinds. The rest are categories the review never
raises.

## Wrong in this repository before

- **A trade-off already written down.** A nearby comment, a doc in `docs/` or a
  test name states the choice (a tie-break order, a shared delivery deadline, a
  fallback wait, a usage line labelled at handling time). Read the comment
  above the code and grep the test names before calling the behavior a defect;
  disagreeing with a stated choice is a question to the maintainer, not a
  finding.
- **Unchanged code.** A hunk that only moved a function between files, or code
  identical on the base, is not this PR's to fix. Compare with the base before
  raising anything on moved lines; a defect there is a separate issue.
- **A race that cannot interleave.** In one page, nothing runs between two
  JavaScript statements with no `await` (or callback boundary) between them,
  and in Rust a check and an act under one held guard do not race. Name the
  await, the second tab or the second thread the other side runs in, or drop
  it. The runtime is Tokio's multithreaded one, so in Rust "no `.await`
  between them" is not enough on its own: an atomic read then written, or two
  separate lock acquisitions, can interleave across worker threads.
- **A claim about a tool or platform, unverified.** axum path segments,
  Python version syntax, what `gh` accepts, what a workflow context holds, the
  Playwright library's default timeout, which runner has which tool. Read the
  pinned version's source or docs, or the CI log, before asserting it, and
  never say a test fails without a run that shows it failing.
- **Already moot.** The file was reverted, the cause fixed one level down, or
  the test replaced after a rebase. Re-read the head, not the first diff seen.
- **Problem-bank material as leakage.** Judge files keep published cases on
  purpose, page examples are rendered from the judge's own cases, and a
  `source` title shown behind the opt-in is by design. Only something a
  candidate sees before or during the interview that names the problem or its
  technique leaks ([judge.md](judge.md)).
- **Hardening inside the user's own trust boundary.** Symlinks or ACLs in the
  user's data directory, a count the candidate claims that only lifts a
  refusal, files copied from the tracked tree into fixtures. Whoever can plant
  the file already holds what the hardening would protect.
- **A rule widened to paths that do not exist.** Before asking a hook, a gate
  or a pattern to cover more, check that the files it would cover exist.
- **A doc sentence applied to its neighbor.** A contract line about one case
  (volunteered hints) read as binding another (withheld hints). Quote the
  sentence and check it names the case.

## Never raised

- What the gate reports: formatting, lint, generated-artifact drift, a failing
  test CI already shows.
- Docstrings, type annotations, unused imports, narrower exception types,
  renames for taste, or a dependency bump, unless the diff's own behavior
  depends on it.
- "Verify that", "ensure that" or "consider" with no defect behind it. A
  finding states what goes wrong; a doubt with no trigger is a question.
- Something "may be defined elsewhere" or "may be unused": grep, then either
  state what the search found or say nothing.
- Process asks, which are not code review and have their own owners: a rebase
  (the sweep's rebase request), splitting unrelated changes into their own
  commit, a PR description that does not match the diff, `make indent`,
  trailing whitespace, vendored bytes without a license or checksum. The
  commit series and the description are still read, and said once in the
  report, not on a line.
- A follow-up the PR description already names as out of scope: ask for an
  issue, not a change, and only if none exists. A description never exempts a
  defect the diff itself introduces.
