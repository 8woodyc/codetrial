#!/bin/sh
# Read-only snapshot of the open pull requests for the triage sweep. Writes
# nothing to GitHub; pulls.md says which of the actions it lists a sweep applies
# on its own and which wait for the user.
#
# Usage: pr-sweep.sh [repo] [stale-days] (default sysprog21/codetrial, 7)
#
# Prints one JSON object:
#   approve   runs waiting for approval on the current head of an open PR, and
#             whether its author is a first-time contributor
#   outdated  unresolved review threads whose lines the PR no longer touches
#   overlap   PR pairs that close the same issue
#   stale     PRs, not the viewer's or a deleted account's, whose last visible
#             human review or comment from someone other than the author is
#             newer than anything the author did and older than stale-days,
#             unless the latest verdict is an approval, it is the rebase
#             request on a PR that still conflicts, or the activity was cut
#             off at 100
#   rebase    non-draft PRs against the default branch that conflict with it
#             and whose head moved since the last rebase request, or that never
#             got one; a changes-requested review on the current head that
#             mentions rebase in any other wording also counts as the request
#   hide      earlier rebase requests that a newer one supersedes, including
#             every one on a PR in rebase, which gets a new request, and on a
#             conflicting PR every one left on an older head once any rebase
#             request sits on the current head
#   hide_ask  other changes-requested reviews mentioning rebase on a PR that
#             has or is about to get the fixed one: hand-written ones, and
#             fixed-text ones with inline comments; they may carry more
#   unknown   PRs whose mergeability GitHub has not computed yet; rerun
#   prs       number, title, updatedAt, head, author, closing issues and files
#             of every open PR, for judging overlap that shares no issue and
#             for checking a PR again before writing to it

set -eu

repo=${1:-sysprog21/codetrial}
days=${2:-7}
me=$(gh api user --jq .login)
text=$(dirname "$0")/rebase.md

# The short request maintainers write by hand asks for a subset of rebase.md, so
# it counts as a rebase request too where the default branch is the main it
# names, but the sweep never posts it.
# shellcheck disable=SC2016
short='Rebase latest `main` branch and resolve conflicts.'

# A thread reply is a review by its writer, so author replies show up in
# reviews. A review records the head it was left on, so a head that moved since
# says exactly that someone pushed after it. pushedDate is always null now, and
# a commit date says when a commit was made rather than pushed, so the latest
# force push or commit date is only the fallback after a plain comment.
# shellcheck disable=SC2016
query='query($owner: String!, $name: String!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef { name }
    pullRequests(states: OPEN, first: 50, after: $endCursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title isDraft authorAssociation headRefOid mergeable baseRefName
        updatedAt headRefName headRepository { nameWithOwner }
        author { login }
        closingIssuesReferences(first: 100) { totalCount nodes { number } }
        files(first: 100) { totalCount nodes { path } }
        commits(last: 1) { nodes { commit { committedDate } } }
        reviews(last: 100) {
          totalCount
          nodes {
            id author { __typename login } state submittedAt isMinimized body
            commit { oid }
            comments { totalCount }
          }
        }
        comments(last: 100) {
          totalCount
          nodes { author { __typename login } createdAt }
        }
        reviewThreads(first: 100) {
          totalCount
          nodes { id isResolved isOutdated path }
        }
        timelineItems(last: 1, itemTypes: [HEAD_REF_FORCE_PUSHED_EVENT]) {
          nodes { ... on HeadRefForcePushedEvent { createdAt } }
        }
      }
    }
  }
}'

pages=$(gh api graphql --paginate -F owner="${repo%/*}" -F name="${repo#*/}" \
    -f query="$query")
prs=$(printf '%s\n' "$pages" | jq -s '[.[].data.repository.pullRequests.nodes[]]')
base=$(printf '%s\n' "$pages" | jq -rs '.[0].data.repository.defaultBranchRef.name')

# Fork runs carry an empty pull_requests array, so a run is tied to its PR by
# head repository, branch and SHA; the SHA alone would let two forks' PRs at one
# commit share a run and its first_time. A pending run on an older SHA is
# superseded and needs nothing. The listing is captured before jq reads it,
# since in a pipe set -e sees only jq, and a failed listing would read as no
# held runs at all.
runs=$(gh api --paginate \
    "repos/$repo/actions/runs?status=action_required&event=pull_request&per_page=100" \
    --jq '.workflow_runs[] | {id, name, head_sha, head_branch,
      head_repo: .head_repository.full_name, actor: .actor.login}')
runs=$(printf '%s\n' "$runs" | jq -s .)

# A rebase request is a visible changes-requested review from someone other than
# the author whose body is exactly rebase.md or the short request and which
# carries no inline comments, since hiding a review collapses what it carries.
# Any other review that mentions rebase may ask for more, so it only ever lands
# in hide_ask, but one left on the current head still keeps the PR out of
# rebase: it already asked, and a second request only adds a notification.
# GitHub refuses a review of one's own PR, so the viewer's PRs are never in
# rebase, and rebase.md names the default branch, so a PR stacked on another
# branch is left to its reviewer.
printf '%s\n%s\n' "$prs" "$runs" | jq -s --argjson days "$days" --arg me "$me" \
    --arg base "$base" --rawfile text "$text" --arg short "$short" '
def human: .author != null and .author.__typename != "Bot";
def ts: if . == null then 0 else fromdateiso8601 end;
def pushed: [.commits.nodes[0].commit.committedDate,
  .timelineItems.nodes[0].createdAt] | map(ts) | max;
def trim: gsub("^\\s+|\\s+$"; "");
def whole: .reviews.totalCount <= 100;
def nudgeable: .author != null and .author.login != $me;
.[0] as $prs | .[1] as $runs | ($text | trim) as $fixed |
def requests: .author.login as $a | [.reviews.nodes[]
  | select(human and .author.login != $a and .state == "CHANGES_REQUESTED"
      and (.isMinimized | not))];
def fixed_only: .comments.totalCount == 0
  and (.body | trim | . == $fixed or ($base == "main" and . == $short));
def asks_rebase: [requests[] | select(fixed_only)]
  | sort_by(.submittedAt | ts);
def mentions_rebase: .body | test("\\brebase"; "i");
def asked_here: .headRefOid as $head | any(requests[];
  .commit.oid == $head and mentions_rebase);
# A conflicting PR whose only request on its current head is hand-written:
# that review is the standing request, so no fixed one is kept beside it and
# it is never offered for hiding.
def hand_asked: .headRefOid as $head | .mergeable == "CONFLICTING"
  and asked_here and all(asks_rebase[]; .commit.oid != $head);
def needs_rebase: .mergeable == "CONFLICTING" and (.isDraft | not)
  and .baseRefName == $base
  and nudgeable and whole
  and (asked_here | not);
{
  approve: [$prs[] as $p | $runs[] | select(.head_sha == $p.headRefOid
      and .head_branch == $p.headRefName
      and .head_repo == $p.headRepository.nameWithOwner)
    | {pr: $p.number, run: .id, workflow: .name, actor,
       touches_workflows: any($p.files.nodes[].path; startswith(".github/")),
       files_truncated: ($p.files.totalCount > 100),
       first_time: ($p.authorAssociation
         | . == "FIRST_TIME_CONTRIBUTOR" or . == "FIRST_TIMER")}],
  outdated: [$prs[] | .number as $n | .reviewThreads.nodes[]
    | select(.isOutdated and (.isResolved | not))
    | {pr: $n, thread: .id, path}],
  overlap: [$prs[] as $a | $prs[] as $b | select($a.number < $b.number)
    | [$a.closingIssuesReferences.nodes[].number
       | select(IN($b.closingIssuesReferences.nodes[].number))] as $shared
    | select($shared | length > 0)
    | {a: $a.number, b: $b.number, issues: $shared}],
  stale: [$prs[] | select(nudgeable and whole and .comments.totalCount <= 100)
    | .author.login as $author
    | ([(.reviews.nodes[] | select(.isMinimized | not)), .comments.nodes[]]
       | map(select(human))) as $acts
    | ([$acts[] | select(.author.login != $author
          and (.state == "APPROVED" or .state == "CHANGES_REQUESTED"))]
       | max_by(.submittedAt | ts).state) as $verdict
    | ([$acts[] | select(.author.login != $author)
        | {id, by: .author.login, at: (.submittedAt // .createdAt), state,
           commit: .commit.oid}]
       | max_by(.at | ts)) as $review
    | ([$acts[] | select(.author.login == $author)
        | .submittedAt // .createdAt | ts] | max // 0) as $mine
    | ($review.at | ts) as $at
    | (if $review.commit then $review.commit != .headRefOid
       else pushed > $at end) as $pushed_since
    | select($review != null and $verdict != "APPROVED"
        and ((.mergeable == "CONFLICTING"
          and any(requests[]; .id == $review.id and mentions_rebase)) | not)
        and $at > $mine and ($pushed_since | not)
        and now - $at > $days * 86400)
    | {pr: .number, author: $author, review_by: $review.by,
       review_at: $review.at, idle_days: ((now - $at) / 86400 | floor)}],
  rebase: [$prs[] | select(needs_rebase) | {pr: .number, author: .author.login}],
  hide: [$prs[] | .number as $n | select(whole)
    | asks_rebase as $r
    | (if needs_rebase or hand_asked then $r else $r[:-1] end)[]
    | {pr: $n, review: .id, at: .submittedAt}],
  hide_ask: [$prs[] | .number as $n
    | select(whole and (needs_rebase
        or (asks_rebase | length > 0) and (hand_asked | not)))
    | requests[] | select((fixed_only | not) and mentions_rebase)
    | {pr: $n, review: .id, at: .submittedAt, by: .author.login,
       body: (.body | .[0:300])}],
  unknown: [$prs[] | select(.mergeable == "UNKNOWN") | .number],
  prs: [$prs[] | {number, title, updatedAt, head: .headRefOid,
    author: .author.login, isDraft, authorAssociation,
    closes: [.closingIssuesReferences.nodes[].number],
    closes_truncated: (.closingIssuesReferences.totalCount > 100),
    files: [.files.nodes[].path], files_truncated: (.files.totalCount > 100),
    reviews_truncated: (.reviews.totalCount > 100),
    comments_truncated: (.comments.totalCount > 100),
    threads_truncated: (.reviewThreads.totalCount > 100)}]
}'
