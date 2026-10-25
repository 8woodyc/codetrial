# Private local experience

Each CodeTrial skill invocation saves an auditable report in scratch. Reviews
finish through `review-lifecycle.py`; other skills explicitly use
`memory.py experience record`. A combined invocation may list several skills in
one report. These local agent assertions are `trust: local-agent` and
`binding: false`; they never become maintainer rules, canonical GitHub evidence
or public snapshot content. Counts do not demonstrate review quality.

## Prepare and record

`review-bundle.sh` automatically calls `review-lifecycle.py begin --bundle`.
It writes private `$bundle/experience-start.json` with a random run ID and pinned full
manifest head, manifest hash and diff hash; `$bundle/experience-status.json` reports
preparation. Repeated begin at the same identity keeps the run ID. A changed
bundle requires explicit new preparation and review, not reuse of a stale report.
For a manually prepared bundle, explicitly run:

```sh
python3 .claude/skills/codetrial-review/review-lifecycle.py begin --bundle "$bundle"
```

Bundle preparation also calls `memory.py experience context --bundle`, producing
private `experience.md` and `$bundle/experience-context-status.json`. This is a direct
read-only local lookup; it neither migrates nor refreshes canonical history.
It selects at most five confirmed/refuted cases from complete runs on the first
128 sorted exact manifest paths. Read its truncation and unavailable diagnostics.
Current applicability is **UNKNOWN** until the trigger and canonical sources
are checked at the pinned head. This local page supplements canonical
`$bundle/history.md` and `$bundle/memory-status.json`; it cannot replace them or establish that
no history exists. Never publish the local context or its statuses.

Pin the full 40-character examined commit before checking evidence. For review,
use the manifest's head, not a later checkout head. For other skills supply
relevant paths explicitly. Uncommitted work needs its diff and limitations
retained in scratch; recording its HEAD does not certify those bytes as a
reviewed commit.

The following runnable example uses the generic skill record command to save
an incomplete preparation with no claimed findings. It does not finish a review. Set `SCRATCH` to this session's scratch directory first. Complete the
actual work and replace the fields before describing a run as complete.

```sh
mkdir -p "$SCRATCH"
git rev-parse HEAD > "$SCRATCH/experience-head.txt"
python3 - "$SCRATCH" <<'PY'
import json
from pathlib import Path
import sys

scratch = Path(sys.argv[1])
report = {
    "schema_version": 1,
    "head": (scratch / "experience-head.txt").read_text().strip(),
    "outcome": "incomplete",
    "paths": [],
    "skills_used": ["codetrial-review"],
    "findings": [],
    "refuted": [],
    "questions": [],
    "history_evidence": {"used": [], "consulted": [], "rejected": []},
    "coverage": "Preparation only; no source or current-head checks performed.",
    "next_checks": ["Open the target diff, owning contracts and canonical history."],
}
(scratch / "experience.json").write_text(json.dumps(report, indent=2) + "\n")
PY
python3 .claude/skills/codetrial-review/memory.py experience record \
  --skill codetrial-review --report "$SCRATCH/experience.json"
```

For every prepared review, finish with the report after checking all evidence:

```sh
python3 .claude/skills/codetrial-review/review-lifecycle.py finish \
  --bundle "$bundle" --report "$SCRATCH/experience.json"
```

Optional `--db`, `--repo` and `--skill` select the store and invocation; otherwise
store selection honors `CODETRIAL_MEMORY` and the common git directory default.
Finish requires an existing start and the unchanged original manifest, head and
diff; it never begins or restarts a review implicitly. It saves a sanitized
report and configuration privately under the common git directory's
`codetrial-review/pending/<run_id>/` before invoking the record command. Directories
are mode 0700 and report/configuration/attempt/receipt files are 0600.

Read the exit status and receipt, retaining its `run_id` and pending directory.
A busy lock or storage failure returns nonzero and leaves pending data and
`$pending_dir/attempt.json` diagnostics; it does not retry in a loop. After addressing the
failure, explicitly retry the returned directory:

```sh
python3 .claude/skills/codetrial-review/review-lifecycle.py --retry "$pending_dir"
```

Retry refuses a missing or changed original bundle. Successful recording is
idempotent for the same run/report. After a matching durable database receipt,
finish writes the pinned head to the reviewed marker only for a **complete,
committed local** manifest (no PR number), with `codetrial-review` as the
primary or an explicitly declared skill. A non-review skill alone cannot certify
a code review. Dirty, incomplete and PR reviews never
write that marker. No step substitutes current checkout HEAD or executes report
or recovery text. There is no universal skill finalizer: review begin is automated
by bundle preparation, while finish and failure recovery remain explicit.

Generic `memory.py experience record` returns `recorded` or `already-recorded`
on success and does not manage review starts or markers. Retain its report and
failure diagnostics in scratch. Never claim durable recording after a failure
or run an unbounded collection merely to make a local write pass. A report
without history references can be recorded when canonical history is unavailable;
state that limitation rather than inventing references.

## Evidence fields

Always include `schema_version: 1`, full `head`, `outcome` (`complete` or
`incomplete`), `paths`, `skills_used`, `findings`, `refuted`, `questions`, and
`history_evidence`. A complete outcome means the stated scope was completed,
not that no defects exist. Include coverage, checks actually run and limits;
the helper retains these report fields but does not certify their truth.
Optional `base` is another full commit. For bundle recording, manifest files supply
the recorded paths, so case paths must belong to that scope.

A confirmed case in `findings` has `verdict: "confirmed"`, `level` (P0–P3),
`path`, positive `line` when applicable, `claim`, reproducible `trigger`, `fix`,
and a nonempty `evidence` list. Name concrete head paths/lines, commands and
observed outputs, not just a reviewer conclusion. A `refuted` case needs its
claim, concrete contradicting evidence and `reason` or `fix`. `questions` are
unproven cases: state what is unresolved and what check would settle it. Never
call an unperformed check a refutation. Empty arrays are valid for a clean
run; put the checks supporting coverage in the report rather than inventing a
finding. Non-code skills use case paths only when those paths were in scope.

A case may also carry `rule` (the name of the rules file that prompted it,
without its directory), `lens` (one of scope, contracts, trust, failure, tests, register,
ui) and `reviewer` (the model or agent that raised it). They record where a
finding came from so that a later check can say which rules and lenses produced
confirmed findings and which produced refuted ones; absent fields change
nothing. A case's later outcome is still recorded with `experience resolve`, and
`not-reproduced` is the label for a finding later shown to be invalid.

Each canonical history reference is an object in one of these arrays:

- `used`: changed a conclusion; requires `source_key`, `reason` and a concrete
  `current_head_check` naming the examined code and why the source still applies.
- `consulted`: opened and checked but did not change the conclusion; requires
  `source_key`, `reason` and `current_head_check` describing the checked relevance.
- `rejected`: opened and checked, then excluded; requires `source_key` and the
  reason (for example superseded, wrong scope or contradicted at this head), plus
  `current_head_check` identifying the checked mismatch or why code applicability
  is not relevant.

Open canonical records with `memory.py show KEY` and check their source author,
trust, date and content before claiming use. Search hits or a local experience's
quoted source are not substitutes. `source_key` accepts `body:N`, `review:N`,
`review_comment:N` and `issue_comment:N`; the writer verifies their presence
and snapshots provenance/body digest, but cannot prove the operator read them.
A case's optional `source_keys` must each have an evidence-use entry. For example,
a used entry has this shape (replace the illustrative key and text with checked
facts; never record this as evidence unchanged):

```json
{
  "source_key": "review_comment:123",
  "reason": "The opened maintainer ruling changes this finding's verdict.",
  "current_head_check": "At the report head, path:line and its caller still enforce the exact condition described by the ruling."
}
```

Typed path identities that credential masking or hidden-character removal would
change are explicitly refused, never silently aliased to another path. Normal
visible Unicode paths are preserved. JSON inputs reject duplicate keys and
nonfinite values; reports are bounded to 1 MiB.

Credential masking is a safeguard, not permission to retain candidate material
or secrets. Keep them out of report inputs. Reports remain private in the common
git directory's memory database and scratch; never stage or publish them. Recording makes the database and existing SQLite
WAL/journal companions owner-only (`0600`); pending directories are `0700` and
their files and generated local context are `0600`.

## Retrieve and recheck

```sh
python3 .claude/skills/codetrial-review/memory.py experience list --limit 10
python3 .claude/skills/codetrial-review/memory.py experience search retry --file src --limit 10
python3 .claude/skills/codetrial-review/memory.py experience show "$run_id"
```

Search returns complete runs' confirmed/refuted cases by default; explicitly
add `--include-unproven` for questions. It is a bounded local lead search,
not an exhaustive history query. Read the stored report, reopen any canonical
source with `show KEY`, and trace the trigger at the new examined head. A past
agent verdict, even confirmed or resolved, cannot suppress a present finding or
establish maintainer intent. Record that new check in the new invocation's
report. Incomplete runs remain inspectable with `list` and `show`.

After actually checking a later outcome, append it without rewriting the old
report. Set `case_id` to the numeric `id` returned by experience search, set
`checked_head` to the full checked commit, and write a JSON array of concrete
evidence strings to `$SCRATCH/outcome-evidence.json`:

```sh
python3 .claude/skills/codetrial-review/memory.py experience resolve \
  --case "$case_id" --status fixed --head "$checked_head" \
  --evidence "$SCRATCH/outcome-evidence.json"
```

Other statuses are `still-present`, `not-reproduced` and `accepted-risk`.
`accepted-risk` records the checked disposition and its evidence; the local
label itself conveys no maintainer authority. A new push alone is never proof
of a fix. Resolution appends evidence and does not retroactively change the
original verdict, run outcome or reviewed commit marker.
