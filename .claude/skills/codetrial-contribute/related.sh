#!/bin/sh
# Check what a branch is about to meet, run before opening its pull request: the
# issues it names, who holds them, and the open pull requests that close the
# same issues or touch the same files. Changes nothing on GitHub; it fetches
# upstream main unless RELATED_BASE names a ref to compare against instead.
#
# Usage: [RELATED_BASE=ref] related.sh [issue...]
#        Run on the branch, after committing: only commits are read. Issues
#        default to those its commits name after Closes, Fixes, Resolves or
#        Refs, as #N, sysprog21/codetrial#N or the issue URL; a reference to
#        another repository is left out.
#
# Prints one JSON object:
#   base        the upstream main the branch is compared against
#   files       files the branch changes
#   closes      issues its commits close; refs: issues they reference without
#               closing
#   issues      each named issue: state, assignees, and others_hold when it is
#               assigned to someone other than the viewer
#   own_pr      the open PR whose head is this commit, or the viewer's PR from
#               a branch of this name, if one exists already
#   same_issue  other open PRs that close one of the named issues
#   same_files  other open PRs sharing files with the branch, most shared first

set -eu

repo=sysprog21/codetrial
git rev-parse --git-dir > /dev/null 2>&1 || {
    echo "related.sh: run it inside the branch's checkout" >&2
    exit 2
}
me=$(gh api user --jq .login)
branch=$(git branch --show-current)

# Compare against upstream main fetched by URL, which works the same from a
# clone or a fork whatever its remotes are called; the fetch is incremental.
# RELATED_BASE names a ref to use instead, with no fetch, which has to be as
# current as upstream: on a stale one, commits already merged count as this
# branch's.
if [ -n "${RELATED_BASE:-}" ]; then
    base=$RELATED_BASE
else
    git fetch -q "https://github.com/$repo.git" main
    base=$(git rev-parse FETCH_HEAD)
fi

files=$(git diff --name-only "$base"...HEAD | jq -Rn '[inputs | select(length > 0)]')
messages=$(git log --format=%B "$base"..HEAD)
keyword()
{
    printf '%s\n' "$messages" \
        | grep -oiE "\\b($1)[: ]+(#|$repo#|https://github\\.com/$repo/issues/)[0-9]+" \
        | grep -oE '[0-9]+$' | sort -un | jq -Rn '[inputs | tonumber]'
}
closes=$(keyword 'close[sd]?|fix(e[sd])?|resolve[sd]?')
refs=$(printf '%s\n%s\n' "$(keyword 'refs?')" "$closes" | jq -s '.[0] - .[1]')

if [ $# -gt 0 ]; then

    # #N, owner/repo#N and an issue URL, with or without a trailing slash, all
    # end in the number.
    named=$(printf '%s\n' "$@" | sed 's|/*$||; s/.*[#/]//' \
        | jq -Rn '[inputs | tonumber]')
else
    named=$(printf '%s\n%s\n' "$closes" "$refs" | jq -s 'add | unique')
fi

# A lookup that fails marks that issue UNKNOWN instead of dropping it, since a
# pipe would otherwise hide the failure.
issues=$(for n in $(printf '%s\n' "$named" | jq -r '.[]'); do
    gh issue view "$n" --repo "$repo" \
        --json number,title,state,stateReason,assignees \
        --jq '{number, title, state, stateReason,
               assignees: [.assignees[].login]}' \
        || jq -n --argjson n "$n" '{number: $n, state: "UNKNOWN", assignees: []}'
done | jq -s .)

prs=$(gh pr list --repo "$repo" --state open --limit 1000 \
    --json number,title,author,headRefName,headRefOid,files,closingIssuesReferences)
head=$(git rev-parse HEAD)

printf '%s\n' "$prs" | jq --arg me "$me" --arg branch "$branch" --arg head "$head" \
    --arg base "$base" --argjson files "$files" --argjson closes "$closes" \
    --argjson refs "$refs" --argjson named "$named" --argjson issues "$issues" '
# The head commit identifies the PR even when the branch was pushed under
# another name; the name covers a PR whose branch has commits not pushed yet.
def own: .headRefOid == $head or (.author.login == $me and .headRefName == $branch);
def other: select(own | not) | {pr: .number, title, author: .author.login,
  closes: [.closingIssuesReferences[].number], paths: [.files[].path]};
{
  base: $base, files: $files, closes: $closes, refs: $refs,
  issues: [$issues[] | . + {others_hold: any(.assignees[]; . != $me)}],
  own_pr: ([.[] | select(own) | .number] | first),
  same_issue: [.[] | other
    | {pr, title, author, issues: [.closes[] | select(IN($named[]))]}
    | select(.issues | length > 0)],
  same_files: ([.[] | other
    | {pr, title, author, shared: [.paths[] | select(IN($files[]))]}
    | select(.shared | length > 0)]
    | sort_by(-(.shared | length)))
}'
