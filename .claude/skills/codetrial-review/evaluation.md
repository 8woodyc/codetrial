# Frozen retrieval controls

`evaluate-memory.py` measures standalone historical lookups against a frozen
public snapshot. It does not simulate reviewing a target PR, reconstruct an
earlier review cutoff, or establish that memory improves defect detection; see
[defect-evaluation.md](defect-evaluation.md) for what would.
Keep source-conditioned model labels separate from human judgments and
independent user queries. A known-positive set is not exhaustive relevance:
unjudged results are not false positives.

## Freeze inputs before reading rankings

Use Python 3.9+ on Unix. Select a full, trusted local control commit explicitly.
The tool extracts executable `memory.py` and `benchmark-memory.py` from that
commit into private temporary storage. It never executes code from a snapshot
or a query set, never fetches Git objects and never calls GitHub. The snapshot
producer must have identical collector bytes and a compatible index version.

```sh
python3.12 .claude/skills/codetrial-review/evaluate-memory.py cache \
  --snapshot "$SNAPSHOT_DIRECTORY" --control-commit "$CONTROL_COMMIT" \
  > "$SCRATCH/evaluation-cache.json"
```

This verifies local artifact size, digest, exact schema, integrity, repository
and content requirements. It does **not** perform GitHub attestation verification;
its cache is an explicitly local evaluation artifact. Compressed and expanded
sizes are bounded by the validated manifest. The private cache lives under the
Git common directory at `codetrial-review/evaluation/`, grouped by public corpus
and then complete cache identity. Publication and original development stores
are untouched. Creation is locked and atomic; an existing cache must match and
is never overwritten. Its files are read-only, and later runs verify their
hashes and reject journal sidecars rather than migrating the database.

Prepare a query JSON file with schema version 1 and the exact `identity` from
the cache command. Include explicit `judgement_provenance` and a `queries` list.
Each query supplies these fields:

| Field | Meaning |
| --- | --- |
| `id`, `slice`, `split` | Stable identifier, rationale/failure/precedent/inflight content slice, and development or holdout |
| `text` | Frozen query, written before rankings are inspected |
| `review_target_thread` | `null`: these are standalone source lookups |
| `relationship_group` | Lowest thread number in the weak body-closing-reference component; gold co-occurrence also joins components |
| `filters` | Explicit `trust_in`, `kinds`, state, files/symbols/errors, exclusions, since, limit and per-thread limit |
| `gold` | Known-positive `key`, `thread`, public `body_sha256`, plus judgment/source provenance |
| `leakage` | Source conditioning, authoring method and any query/answer overlap already known |

Only maintainer/reported account trust scopes are supported. This classification
is not proof that a human wrote the text. The tool rejects missing, deleted,
projected, edited or out-of-scope gold sources and components crossing the
holdout split. Keys refer to source identity, never insertion-order SQLite IDs.
Keep source URLs, authors, timestamps and judgment provenance in the frozen
input so an independent reader can audit labels. Do not promote generated
summaries to canonical maintainer rationale.

Freeze the query bytes and record their SHA-256 before running search. Do not
add positive labels from retrieved results or revise questions after observing
scores. A new set needs a new identity and explicit explanation. All questions
written from their relevant documents remain source-conditioned even if they
share no exact words. Keep a holdout and decide its final use before opening it.

## Run a control

```sh
EVALUATION_CACHE=$(jq -r .cache "$SCRATCH/evaluation-cache.json")
python3.12 .claude/skills/codetrial-review/evaluate-memory.py run \
  --cache "$EVALUATION_CACHE" --queries "$FROZEN_QUERIES" \
  --queries-sha256 "$QUERIES_SHA256" --control-commit "$CONTROL_COMMIT" \
  --output "$SCRATCH/development-control.json"
```

The default selects development queries only. Explicit `--split holdout` or
`--split all` opens the corresponding experiment; do not use them for tuning.
Output files are created privately and exclusively. The tool reads query bytes
once, verifies that exact buffer's hash and refuses to overwrite output.

Each query runs twice in the same process. Results retain exact keys and order,
known-positive document/thread Recall@1/3/5/10 and reciprocal rank, empty results,
diversity, unjudged keys, mechanical path noise, short-approval and bot counts,
SQL materialized candidates, effective FTS calls, elapsed lookup time and UTF-8
bytes from the pinned search presentation without applicability annotations.
Current controls include retrieval disclosure; legacy controls retain their
original presentation. Rendering reuses measured rows and does not perform a
second selection query. Transaction control is separate from FTS query counts. Duplicate-source
threads are collapsed within the returned document window for thread-level rank;
this is not an independent search for k distinct threads. Recall beyond the
requested document limit is unavailable rather than assigned a score. Declared
human authorship is not independently verified by this tool. The before/after database hash must
match. Code/helper/query identities and SQLite version are recorded. Order
instability is visible per query rather than silently averaged away.

Mechanical overlap annotations add shared query trigrams, exact named
identifiers, path-as-filter use and explicit generated-content markers. They
are diagnostics computed after freezing; they never alter the questions, gold
or declared judgment provenance. Noise heuristics are not relevance labels.
Report per-query/per-slice counts and leakage classes; do not pool these into an
unqualified quality score.

`--mode strict-body` selects body-only questions, limits FTS matching to title
and body, and prevents OR before a second SQL call. Default mode runs the pinned
collector unchanged. This scoped control is a harness policy, not a product
ranking change. Do not attribute an OR-fallback versus strict-query difference
to storage quality. Tokenizers, fields and candidate policies must be disclosed.

## GitHub and checkout comparisons

Freeze each comparison policy and exact commands before its run. For a GitHub
comparison, use the same full query and explicit repository, `in:title,body`
and applicable open/merged qualifiers. Keep maintainer inline rationale separate:
GitHub cannot enforce the same comment-level trust scope. Score the matched body
subset at thread level only. Compare strict conjunctions with the strict-body
control, and label product-default policy comparisons separately.

Retain raw responses privately, request timestamps, elapsed time, response bytes,
rate-limit headers, `total_count`, `incomplete_results`, over-fetch limits and
caps. Post-filter against frozen eligible IDs, body account trust and state;
check returned title/body/author/state against the cache. Returned-item checks
cannot establish drift of nonreturned items or an atomic GitHub snapshot. A
failed, capped or incomplete request is not a complete empty result. Record
unknown coverage rather than assigning a false zero. Follow the official
[search API limits](https://docs.github.com/en/rest/search/search) and
[scope qualifiers](https://docs.github.com/en/search-github/searching-on-github/searching-issues-and-pull-requests).

For checkout coverage, run `git grep` against the pinned commit tree, with fixed
pathspecs excluding externals, vendor and generated problem/judge/fixture trees.
A simple declared baseline is case-insensitive whole-word OR of all unique ASCII
query tokens at least three characters long, without stopword removal. Preserve
argv and output; report matched file/line counts, bytes, time and known source
path availability. This measures current-code coverage, not historical recall.
Missing or moved historical paths are unavailable evidence, not history misses.

Keep source databases, queries, raw captures and reports in private scratch or
the Git-local evaluation cache. Reports are not new tracked repository files.
Signed bootstrap readiness, repeated agent trajectories, time-correct evidence
and blinded human review quality remain separate experiments.
