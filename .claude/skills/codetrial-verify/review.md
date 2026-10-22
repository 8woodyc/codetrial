# Review a diff

A green gate says the change builds and its tests pass. A review asks what the
tests do not, and most of what goes wrong here breaks a contract in `docs/`
rather than a test. The review is a local report: it changes nothing and posts
nothing until the user picks what to fix or what to post. Order governs how the
report reads, never when a finding is told: a leaked key or candidate material
in a public PR is said the moment it is seen.

## 1. Scope

`$pr` is the PR number; `$SCRATCH` is defined in
[codetrial-conventions](../codetrial-conventions/SKILL.md#working-docs-at-the-repo-root).

```sh
gh pr diff "$pr" --repo sysprog21/codetrial --name-only
gh pr diff "$pr" --repo sysprog21/codetrial > "$SCRATCH/pr-$pr.diff"
```

Group the changed files by the area they touch, an area being a row of the
table in step 2 or "other", and review one area at a time; past about 500
changed lines, report per area. A wire change is one area however its files
split: review the `web/` and Rust sides of a topic together. Read the PR's
linked issue and the maintainer comment that accepted its scope
([collaboration.md](../codetrial-contribute/collaboration.md#start-with-an-actionable-contribution)):
that comment is the specification, and work outside it is a finding. When the
issue was only assigned, its body's scope stands in, and that is worth saying.
Write the specification down as a checklist now, one line per thing it asks
for, so step 4 can report against it.
With no target named, review the working tree when it is dirty, else
`upstream/main...HEAD`, naming the remote as the rebase recipe in
[codetrial-contribute](../codetrial-contribute/SKILL.md#answer-a-review) does;
ask only when neither resolves.

Read what the PR already carries before judging it:

```sh
gh pr view "$pr" --repo sysprog21/codetrial \
  --json headRefOid,commits,reviews,comments
gh api graphql --paginate -F n="$pr" -f query='query($n: Int!, $endCursor: String) {
  repository(owner: "sysprog21", name: "codetrial") { pullRequest(number: $n) {
    reviewThreads(first: 100, after: $endCursor) {
      pageInfo { hasNextPage endCursor }
      nodes { isResolved path line originalLine
        comments(first: 1) { nodes { author { login } body } } } } } } }' \
  --jq '.data.repository.pullRequest.reviewThreads.nodes[]
    | [.isResolved, .path, (.line // .originalLine),
       .comments.nodes[0].author.login, .comments.nodes[0].body] | @tsv'
```

A finding an unresolved thread or a cubic comment already raises is cited, not
raised again; a resolved one is raised again only if the head reintroduced it.
The commit series is part of the review, since merges keep it as pushed: name
commits to fold and messages that say nothing.

Done when every changed file sits in exactly one area and the accepted scope is
in hand, or its absence is itself a finding.

## 2. Contracts

A document that refuses a change is the answer (`CONTRIBUTING.md`, Before
changing code). For each area, open its owner and check the change against the
text, never against a summary:

| The diff changes | Owner |
| --- | --- |
| what an interview spends, caches or retries upstream | [provider cost and degradation](../../../docs/provider-cost-and-degradation.md) |
| what a report says about a candidate | [observable delivery policy](../../../docs/observable-delivery-policy.md), [integrity evidence](../../../docs/integrity-evidence.md) |
| what happens to candidate media | [recording contract](../../../docs/recording-contract.md) |
| prompts, the report schema, `tests/golden/` | [interview contract versions](../../../docs/interview-contract-versions.md) |
| how long an interview runs, what the endpoints enforce | [interview length](../../../docs/interview-length.md) |
| a data-channel topic | [codetrial-web](../codetrial-web/SKILL.md) and its wire fixtures |
| the avatar | [avatar contract](../../../docs/avatar-contract.md) |
| `problem-bank/` | [adding a problem](../../../docs/adding-a-problem.md) |
| timing or latency logs | [turn timing](../../../docs/turn-timing.md), [latency observability](../../../docs/latency-observability.md) |
| who may start an interview | no document; find the tests that exercise admission, which are its contract, and expect a change to bring one that fails without it |

An area with no row may still have an owner, since the rest of `docs/` owns
its area the same way: search `docs/` for it before concluding there is none,
and then it answers to the code around it and the tests that pin it.
An owner binds only what its text covers: behavior next to it that it does not
mention has no owner, and a change there is worth a sentence in the PR body.

Done when every area names its owner, or says it has none, and the change was
read against that owner's text.

## 3. What the gate cannot see

The gate already runs the formatters, the linters and the drift checks; never
report what it reports. For each area, look for:

- Trust boundaries. Input from the browser, a webhook or a provider reaching a
  path, a query, a shell or a log unchecked. A key, a token or candidate
  material landing in a log, a report, a fixture or a public thread. Admission
  that a typed username, a replayed request or a missing signature gets past.
- Failure paths. A provider error, a dropped connection or a rejected promise
  swallowed so the interview carries on wrong instead of saying what failed. A
  retry the cost contract does not allow.
- Boundaries. An empty transcript, a zero-length recording, a session ending
  mid-reconnect, the first and the last turn, a timer at zero.
- Ordering. A resume racing the message it replays, a check then an act across
  an await, a message handled before the state it assumes.
- Tests. A new branch no test reaches; a surviving mutant in code the diff adds
  (CI's mutation job names them, as on #262; it is skipped when the diff
  changes no Rust, so `web/` code has no mutation evidence and each new branch
  needs a test that reaches it); a browser test of the shapes
  [SKILL.md](SKILL.md) says reviewers reject (Writing a test).
- Removal. Code the change leaves unused goes in the same PR with its call
  sites, never an alias or a deprecation (`CONTRIBUTING.md`, Before changing
  code); defer only what a versioned contract still promises, and name the
  version bump that frees it.
- Workflows. A diff to `.github/`, to `scripts/test.sh` or to a script a lane
  runs executes on every PR's runner, so it keeps what makes a fork PR safe to
  run here. `.github/workflows/check.yml` runs on `pull_request` with
  `contents: read`; a new `pull_request_target`, `workflow_run` or
  `issue_comment` trigger, or a job
  that raises `permissions`, runs with the base repository's token and is P0
  until a maintainer says otherwise. A value a PR author controls (a title, a
  body, a branch name, `github.head_ref`) reaches a shell through `env:`, as
  `BASE_SHA` does, never as `${{ }}` inside `run:`. `secrets.` stays out of any
  job a PR triggers; `release`, the one job with `contents: write`, runs only
  on a push to `main`. A new third-party action is named with what it gets, and
  a moved tag on an existing one is a change too. A change under
  `.github/actions/` runs inside every job that uses it. PR caches are scoped to
  the PR's ref, so a cache step is a finding only when it restores something a
  later step executes without checking it. `actionlint` in the gate checks
  syntax, not any of this. The trigger, default-permission and release facts
  above are pinned by
  `test_workflow_facts_the_review_checklist_states_still_hold` in
  `tests/test_skill_scripts.py`, so a workflow change that breaks one fails
  there; the `env:` and `secrets.` rules are not, and are read in the diff.
- Skills. A diff that renames or removes a gate, a lane, a CI job, a script,
  a flag or a make target, or changes a maintainer wording, leaves stale the
  skill that names it; run `scripts/check-skill-refs.py` and reread the skills
  that name it. A new file moves nothing.

Done when each area has been read against every item above, with a finding or
a stated nothing.

## 4. Report

| Level | Meaning here | Before merge |
| --- | --- | --- |
| P0 | leaks a key or candidate material, admits an unadmitted caller, loses or corrupts a recording or a report, changes the interview contract without a version bump | blocks |
| P1 | breaks an interview flow or a contract in `docs/`, or a regression a user would hit | fix in this PR |
| P2 | a test gap, a surviving mutant, a silent failure path harmless today | fix here or file an issue |
| P3 | a clarity point the gate does not enforce | optional |

Report two things, kept apart because they fail separately: the checklist from
step 1, each line met, partly met or missing with the `file:line` that shows
it, plus anything the diff does beyond it; then the findings against the
contracts and step 3. A PR can satisfy its issue and break a contract, or keep
every contract and miss half the issue.

Lead the findings with the counts by level, then each one most severe first:
`file:line`, the input or state that goes wrong, and the fix. A finding with no
concrete failure is a question; ask it as one. A clean review names what was
checked, what was not (a live interview, a provider, a browser the tests do not
run) and the residual risk.

The levels order the local report and stop there. A thread on GitHub carries a
correction, a measurement or nothing, with no severity table and no pasted
walkthrough (`CONTRIBUTING.md`, Pull requests): each posted finding is one
inline comment in plain words, under the GitHub rule in
[codetrial-conventions](../codetrial-conventions/SKILL.md).

When the review ran through gh-review, post through it. A review done here
posts as one review, so the author gets one notification. Check every anchor
against the diff first, which moves a finding on a line outside every hunk
into the body instead of letting it fail the whole review, and show the user
the checked text, moved findings included, with the PR's `updatedAt`:

```sh
.claude/skills/codetrial-verify/review-anchors.sh "$pr" "$SCRATCH/review-$pr.json" \
  > "$SCRATCH/review-$pr.checked.json"
```

The review is `event: "COMMENT"` on `commit_id` (the head the findings were
read at), with a one-sentence `body`, which GitHub requires, and one entry in
`comments` per finding, each with `path`, `line` (the line in the file), `side`
(`RIGHT` for an added or unchanged line, `LEFT` for a deleted one) and its own
`body`. A finding that sits in a moved line reads differently in the body, so
reword one that leaned on "this line" before showing it. Once the user
confirms, post the checked file:

```sh
.claude/skills/codetrial-verify/post-review.sh "$pr" \
  "$SCRATCH/review-$pr.checked.json" "$updated_at"
```

It posts nothing and exits 3 when the head moved, the PR was updated since
`$updated_at`, or a comment would still land outside the diff. A moved head
means the findings are read again at the new one; an update at the same head
means the new comment or edit is read first, since it may answer a finding.
When posting is not allowed in this run, leave the payload at
`$SCRATCH/review-$pr.json` and say so.

Past about 500 changed lines, or when several reviewers are run, split them
by lens rather than by file, each reading the whole diff for one thing: the
specification checklist; the owners in step 2; trust boundaries, workflows and
what reaches a log or a public thread; failure paths and boundaries; tests,
including what CI's mutation job says survived; and comments and messages
against the register in
[codetrial-conventions](../codetrial-conventions/SKILL.md). A reviewer handed a
lens reports only that lens, so silence on the others is not a pass.

When more than one reviewer ran, merge duplicates, note how many agreed, and
keep disagreements visible; agreement ranks nothing, a concrete failure does.
A fix that grows past its finding stops for a new answer. Report each as
fixed, partly fixed or unverified, with the command or test that shows it,
never as "addressed".

Done when the user has chosen which findings to act on or post: all, P0 and
P1, named ones, or none.
