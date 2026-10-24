# Workflows, hooks, build and the skills

A diff to `.github/`, to `scripts/test.sh`, to a script a lane runs, to the
hooks or to `.cargo/` runs on every PR's runner or decides what CI proves.

## CI and mutation config must cover what the release builds

Applies to: `.github/workflows/*.yml`, `.cargo/config.toml`,
`.cargo/mutants.toml`, `build.rs`, `scripts/git-*.sh`

Workflow conditions keyed on one exact triple drift from `.cargo/config.toml`'s
`cfg(target_vendor = "apple")` and miss a sibling target a matrix entry adds
later; match the vendor, as `contains(matrix.target, '-apple-')` does. A `RUSTFLAGS` env var
replaces the config flags instead of adding to them. Release-only jobs (`push`
to `main`) find `build.rs` regressions only after the merge, so put a cheap
assertion in the PR `check` job. A refusal step in `check` does not stop
`plan-mutants`, which has its own `if`. Every `exclude_re` entry in
`.cargo/mutants.toml` needs a reason that holds (`participant: None` events can
be built in unit tests). Shell and git calls must survive older git
(`--path-format` arrived in 2.31, and an unknown option exits 0), `core.abbrev`
below 7, unanchored host regexes, and interpreter paths containing spaces in
shebangs.

Seen: 7 PRs. A macOS link flag that nothing verifies, while the build job is
skipped on PRs.

Not a finding: an exclusion that copies an existing pair (a URL wrapper vs an
`_at` seam that a local-server test drives).

## Keep what makes a fork PR safe to run

Applies to: `.github/**`, `scripts/test.sh`, any script a lane runs

`.github/workflows/check.yml` also runs on `pull_request` with `contents: read`; a
new `pull_request_target`, `workflow_run` or `issue_comment` trigger, or a job
that raises `permissions`, runs with the base repository's token and is P0
until a maintainer says otherwise. A value a PR author controls (a title, a
body, a branch name, `github.head_ref`) reaches a shell through `env:`, as
`BASE_SHA` does, never as `${{ }}` inside `run:`. `secrets.` stays out of any
job a PR triggers; `release`, the one job with `contents: write`, runs only on
a push to `main`. A new third-party action is named with what it gets, and a
moved tag on an existing one is a change too. A change under `.github/actions/`
runs inside every job that uses it. PR caches are scoped to the PR's ref, so a
cache step is a finding only when it restores something a later step executes
without checking it. `actionlint` in the gate checks syntax, not any of this.
The trigger, default-permission and release facts above are pinned by
`test_workflow_facts_the_review_checklist_states_still_hold` in
`tests/test_skill_scripts.py`, so a workflow change that breaks one fails
there; the `env:` and `secrets.` rules are not, and are read in the diff.

Not a finding: a step a PR adds that only reads the checkout with the default
`contents: read` token.

## A renamed surface leaves a skill stale

Applies to: `scripts/**`, `Makefile`, `.github/workflows/**`,
`.claude/skills/**`

A diff that renames or removes a gate, a lane, a CI job, a script, a flag or a
make target, or changes a maintainer wording, leaves stale the skill that names
it. `scripts/check-skill-refs.py` runs in the gate and catches a rename; a
changed meaning it cannot see, so reread the skills that name what moved. A
skill script that changed needs its case in `tests/test_skill_scripts.py`, and
a skill description that changed needs `scripts/check-skill-routing.py` run
with that skill and its neighbors.

Not a finding: a new file, which moves nothing.
