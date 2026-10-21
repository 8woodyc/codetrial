# Retrieve and close out review feedback

What a request means, how to answer it and the local list of open requests are
in [Answer a review](SKILL.md#answer-a-review); this file only covers fetching
every thread and confirming the result. A request only to review or summarize a
PR stays read-only. When the user names a subset of comments, address that
subset; "address all the feedback" is the whole list, not a prompt to ask which.

Run `gh auth status` first, so an access failure is reported as unreviewed
feedback rather than as a PR with none. Top-level comments and reviews come from
`gh pr view`; inline threads need their own query, with the ids and URLs that
replying and resolving need:

```sh
gh pr view "$pr" --repo sysprog21/codetrial \
  --json number,url,headRefOid,comments,reviews
gh api graphql --paginate -F n="$pr" -f query='query($n: Int!, $endCursor: String) {
  repository(owner: "sysprog21", name: "codetrial") { pullRequest(number: $n) {
    reviewThreads(first: 100, after: $endCursor) {
      pageInfo { hasNextPage endCursor }
      nodes { id isResolved isOutdated path line originalLine
        comments(first: 100) {
          pageInfo { hasNextPage }
          nodes { id author { login } body url } } } } } } }'
```

`--paginate` follows only the outer `pageInfo` and prints one JSON document per
page. A thread whose `comments.pageInfo.hasNextPage` is true is incomplete;
refetch all of its comments, where the thread's own `pageInfo` is the only one,
before reading its last reply:

```sh
gh api graphql --paginate -F thread="$thread" -f query='query($thread: ID!, $endCursor: String) {
  node(id: $thread) { ... on PullRequestReviewThread {
    comments(first: 100, after: $endCursor) {
      pageInfo { hasNextPage endCursor }
      nodes { id author { login } body url } } } } }'
```
An outdated position does not mean fixed; compare the request with the current
code. Verify a bot finding by the same standard as any other
([collaboration.md](collaboration.md#review-evidence-including-bot-findings)).

Fix each request with the smallest change and run the affected check, then the
full gate with its skips read; browser behavior goes through
[Browser investigation](../codetrial-verify/SKILL.md#browser-investigation)
and red checks through
[Failed GitHub Actions checks](../codetrial-verify/SKILL.md#failed-github-actions-checks).
If the scope changed, update the PR description to describe the final result.

A push does not prove every request addressed. After it, rerun both queries
above and the checks, so a new top-level review or comment is read as well as
a new thread, and reassess anything a new head or new comment touches.
Replies and resolving follow
[codetrial-conventions](../codetrial-conventions/SKILL.md#pull-request-branches-and-review-replies)
and `CONTRIBUTING.md`, which closes an addressed thread with "Resolve
conversation"; never resolve a disputed or unverified request.
