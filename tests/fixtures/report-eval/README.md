# Synthetic report evaluation

These authored fixtures probe report stability and policy invariants. They do
not calibrate the rubric or measure hiring quality, fairness or correctness.
The seven base cases adapt `tests/fixtures/framework-report-cases.json` and
the recognition-recovery probe in `tests/unit/gemini.rs`; no candidate data is
used. Manifest version 2 adds authored artifact-citation checks. Phase labels
are authored expectations, not human ratings. A missing answer after a clear
opportunity can be assessable; a skipped phase is not.

The durable journal runner requires a Unix host and a filesystem supporting
directory fsync. Pure corpus and metric tests also run on other platforms; an
explicit unsupported-platform invocation fails instead of reporting an empty
probe as passed. Normal application builds are unaffected.

Run credential-free checks with `cargo test --lib report_eval`. The ignored
`report_eval_run` test calls the production report transport, repairs,
validator and salvage under its normal total timeout. Regeneration is
excluded. Any transport failure or missing numeric provider `totalTokenCount`
stops spending, so a truncated row cannot stand in for the normal production
retry outcome. Stopped and failed rows are excluded from stability metrics,
with counts shown. Each comparison is made against the item's lowest-numbered
complete repeat, so a repeat 0 that failed a check falls back to the next one;
`items_without_baseline` names any item with results but no complete repeat,
whose rows then enter no comparison. Identity sensitivity is
`identity_minus_repeat_mean_coding`, the swapped-name mean coding score minus
the repeat mean, read against `repeat_coding_spread`; it is null with fewer
than two repeats, which leave no spread to read it against. Phase-label checks
apply only to the unpatched phase-label variant. ASR checks recognize specified forbidden claims; they cannot prove
the absence of every possible invented weakness. Inspect the retained
synthetic reports as well as machine checks before interpreting a result.

The input is fixed coding mode, Python, without boards or drawings. Variants
cover exact repeats, formatting, local renames, a paraphrase, independent
statement ordering, a genuine correctness change, transcript-only synthetic
names, and missing/degraded transcripts. The semantic family is a positive
sensitivity control, not a requirement that the model move a specific number
of points. Identity changes are synthetic sensitivity, never subgroup MAE.

Use a complete isolated project's normal CodeTrial config and a scratch output
path. The first key alone is used, with no key rotation or implicit
default-config fallback. Budgets are explicit and cumulative across resumes.
Calls have a hard ceiling; the token ceiling stops new calls when observed
total tokens reach it and can overshoot by one call. Unknown usage makes the
observed total a lower bound and stops the run. No currency cost is inferred.

For a bounded two-repeat pilot (substitute the isolated config and output):

```sh
export CODETRIAL_REPORT_EVAL_CONFIG=/absolute/path/to/isolated.env
export CODETRIAL_REPORT_EVAL_RESULTS=/tmp/report-eval-pilot.json
export CODETRIAL_REPORT_EVAL_CALL_CAP=10
export CODETRIAL_REPORT_EVAL_TOKEN_CAP=100000
export CODETRIAL_REPORT_EVAL_ITEMS=1
export CODETRIAL_REPORT_EVAL_REPEATS=2
export CODETRIAL_REPORT_EVAL_FAMILIES=repeat
cargo test --lib report_eval_run -- --ignored --nocapture
```

`CODETRIAL_REPORT_EVAL_MODEL` and `CODETRIAL_REPORT_EVAL_SEED` override the
configured report model and normal fixed seed. Each model/seed uses its own
output path and identity; never pool seed variation with fixed-seed repeats.
Omit `CODETRIAL_REPORT_EVAL_FAMILIES` to select all six families. Selection
always requires a repeat control. Items are taken in manifest order; a repeat
count is applied to each selected variant. Invariant failures are retained and
collection continues; they end the invocation with a failed test and their
synthetic report retained.

To test clean resume, set `CODETRIAL_REPORT_EVAL_STOP_AFTER_ROWS=1` on the
first invocation, unset it, then rerun with `CODETRIAL_REPORT_EVAL_RESUME=1`.
This per-invocation stop control does not change the frozen plan or ceilings.
Completed rows are not re-spent. The journal `<results>.attempts.jsonl` is
authoritative; the JSON summary is atomically derived from it. Both files must
be retained.

An interrupted HTTP call has unknown cost. Its reservation is synced before
sending; a crash or timeout leaves durable evidence that forbids automatic
resume. A stop after a 429 or 503, which refuse a request before any
generation, or after a failure whose usage was reported leaves the ledger
exact, so it can be resumed: the stopped row is retried against the same
cumulative ceilings. Row counts describe each row's final outcome; the
stopped attempt stays in `rows` and is counted in `retried_rows`. Other HTTP
statuses, such as a 500 or a 504 deadline, unknown usage on a response, a
decode failure, a lost send, budget exhaustion, unfinished generations, torn
journals, changed inputs, budgets, sources or executable refuse resume. A new
run requires an explicit new path and budgets. Resume deliberately pins the
exact test binary, including dirty source: rebuilding can invalidate a saved
run.

A sidecar `<results>.eval-lock` excludes concurrent writers. A results path
that itself ends in a sidecar suffix is refused. A process kill can leave a
stale lock: remove it only after confirming no writer remains; removing it
does not make an interrupted journal resumable. Synthetic results stay outside
the repository. No real interview material may be substituted into this
corpus.
