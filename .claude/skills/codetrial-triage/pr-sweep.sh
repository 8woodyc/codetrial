#!/bin/sh
# Read-only snapshot of the open pull requests for the triage sweep. Writes
# nothing to GitHub; pulls.md says which of the actions it lists a sweep applies
# on its own and which wait for the user.
#
# Usage: pr-sweep.sh [repo] [stale-days] (default sysprog21/codetrial, 7)
#
# Prints one JSON object:
#   maintainers_known  false when the push listing failed and the COLLABORATOR
#             badge, which read-only collaborators also carry, stood in for it
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
#   rebase    conflicting PRs with no current rebase request, to ask
#   hide      superseded rebase requests in a known wording, to hide
#   hide_ask  other rebase requests, to confirm before hiding
#             (all three: pulls.md, "Conflicting branches")
#   unknown   PRs whose mergeability GitHub has not computed yet; rerun
#   prs       number, title, updatedAt, head, author, closing issues and files
#             of every open PR, for judging overlap that shares no issue and
#             for checking a PR again before writing to it

set -eu

repo=${1:-sysprog21/codetrial}
days=${2:-7}

# No fallback when /user cannot be read, unlike issue-sweep.sh: an unknown
# viewer would put the viewer's own pull requests in rebase, a list a sweep
# posts to without asking.
me=$(gh api user --jq .login)

# Who may push, which is who may ask for a rebase. The COLLABORATOR badge also
# marks read-only collaborators, so it stands in only when this listing, which
# needs push access, fails; gh prints a refusal's body to stdout, so only a
# successful call is read.
if logins=$(gh api --paginate "repos/$repo/collaborators?permission=push" \
    --jq '.[].login' 2> /dev/null); then
    pushers=$(printf '%s\n' "$logins" | jq -Rn '[inputs | select(length > 0)]')
else
    pushers=null
fi
text=$(dirname "$0")/rebase.md

# Shorter wordings maintainers type by hand for the request rebase.md makes;
# matched, like rebase.md, after trimming and dropping a final period.
# shellcheck disable=SC2016
known='["Rebase latest `main` and resolve conflicts",
  "Rebase latest `main` branch"]'

# A thread reply is a review by its writer, so author replies show up in
# reviews. A review records the head it was left on, so a head that moved since
# says exactly that someone pushed after it. pushedDate is always null now, so
# after a plain comment the push is dated by GitHub's clock: the head commit's
# first check suite, created when the push reached a workflow or app, or the
# latest force push. The commit date, which the contributor's clock sets and
# which can run ahead and make every later request look stale, is used only for
# a head with no check suite. A PR force-pushed back to its base has no commit
# at all, and dates only its force push.
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
        commits(last: 1) {
          nodes { commit { committedDate
            checkSuites(first: 10) { nodes { createdAt } } } }
        }
        reviews(last: 100) {
          totalCount
          nodes {
            id author { __typename login } authorAssociation state
            submittedAt isMinimized body
            commit { oid }
            comments { totalCount }
          }
        }
        comments(last: 100) {
          totalCount
          nodes {
            id author { __typename login } authorAssociation createdAt
            isMinimized body
          }
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

# What counts as a rebase request, when it is current, and which copies are
# hidden or only offered for hiding: pulls.md, "Conflicting branches".
printf '%s\n%s\n' "$prs" "$runs" | jq -s --argjson days "$days" --arg me "$me" \
    --arg base "$base" --rawfile text "$text" --argjson known "$known" \
    --argjson pushers "$pushers" '
def human: .author != null and .author.__typename != "Bot";
def ts: if . == null then 0 else fromdateiso8601 end;
def pushed: (.commits.nodes[0].commit // {checkSuites: {nodes: []}}) as $c
  | [([$c.checkSuites.nodes[].createdAt | ts] | min) // ($c.committedDate | ts),
     (.timelineItems.nodes[0].createdAt | ts)] | max;
def trim: gsub("^\\s+|\\s+$"; "");
def norm: trim | sub("\\.$"; "");
def whole: .reviews.totalCount <= 100 and .comments.totalCount <= 100;
def nudgeable: .author != null and .author.login != $me;
def staff: if $pushers != null then .author.login | IN($pushers[])
  else .authorAssociation | IN("OWNER", "MEMBER", "COLLABORATOR") end;
.[0] as $prs | .[1] as $runs | ([$text | norm] + $known) as $wordings |
def requests: .author.login as $a | .headRefOid as $head | pushed as $p |
  [(.reviews.nodes[]
     | select(.state == "CHANGES_REQUESTED"
         and (.body | test("\\brebase"; "i")))
     | {at: .submittedAt, inline: .comments.totalCount,
        current: (.commit.oid == $head)} + .),
   (.comments.nodes[]
     | select(.body | test("^\\s*(@[\\w-]+[,:]?\\s+)*rebase\\b"; "i"))
     | {at: .createdAt, inline: 0, current: ((.createdAt | ts) >= $p)} + .)]
  | map(select(human and staff and .author.login != $a and (.isMinimized | not))
      | {id, by: .author.login, at, body, inline, current});
def fixed_only: .inline == 0 and (.body | norm | IN($wordings[]));
def asks_rebase: [requests[] | select(fixed_only)] | sort_by(.at | ts);
def asked_here: any(requests[]; .current);
# A PR whose only request on its current head is hand-written, whether or not
# it still conflicts: that request is the standing one, so no fixed one is kept
# beside it and it is never offered for hiding.
def hand_asked: asked_here and all(asks_rebase[]; .current | not);
def needs_rebase: .mergeable == "CONFLICTING" and (.isDraft | not)
  and .baseRefName == $base and $base == "main"
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
  stale: [$prs[] | select(nudgeable and whole)
    | .author.login as $author
    | ([.reviews.nodes[], .comments.nodes[]]
       | map(select(human and (.isMinimized | not)))) as $acts
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
          and any(requests[]; .id == $review.id)) | not)
        and $at > $mine and ($pushed_since | not)
        and now - $at > $days * 86400)
    | {pr: .number, author: $author, review_by: $review.by,
       review_at: $review.at, idle_days: ((now - $at) / 86400 | floor)}],
  rebase: [$prs[] | select(needs_rebase) | {pr: .number, author: .author.login}],
  hide: [$prs[] | .number as $n | select(whole)
    | asks_rebase as $r
    | ($r | map(.current) | rindex(true) // -1) as $keep
    | (if needs_rebase or hand_asked then $r else $r | del(.[$keep]) end)[]
    | {pr: $n, id, at, by}],
  hide_ask: [$prs[] | .number as $n
    | select(whole and (needs_rebase
        or (asks_rebase | length > 0) and (hand_asked | not)))
    | requests[] | select(fixed_only | not)
    | {pr: $n, id, at, by, body}],
  maintainers_known: ($pushers != null),
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
