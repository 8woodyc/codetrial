#!/bin/sh
# Post the similar-work notices of one sweep review draft (review-prs.md,
# "Verify and post each PR as it lands"): read both threads before posting
# either, decide each side on its own thread, never post a pair twice in a
# sweep, and skip a pair when either head moved since its diff was read.
#
# Usage: post-similar.sh [--check] <pr> <draft.json> [repo]
#        --check only validates the draft (exit 0, or 2) and posts nothing.
#        SCRATCH names the sweep's scratch directory, which holds the
#        similar-posted ledger of pairs. Prints one JSON object per entry:
#        {action: "similar", pr, number, result, why} with result applied,
#        linked (both threads already link each other), pair (posted earlier
#        this sweep), held (another finish holds the pair and has not posted),
#        moved (a head differs from the one read) or failed.
#        Exits 1 when any entry is moved or failed, so the caller leaves the
#        head out of the review ledger; 2 on bad arguments.
#
# A draft entry is {kind: "pr"|"issue", number, notice, other_notice,
# other_sha}. other_sha is the other PR's head its diff was read at, required
# for kind pr, since the other thread gets a post too. Posts only when a head
# check passes; a thread "links" the other when its body or any comment names it
# as #N.

set -eu

usage='usage: post-similar.sh [--check] <pr> <draft.json> [repo]'
check=false
if [ "${1:-}" = --check ]; then
    check=true
    shift
fi
[ $# -ge 2 ] || {
    echo "$usage" >&2
    exit 2
}
pr=$1
draft=$2
repo=${3:-sysprog21/codetrial}
scratch=${SCRATCH:-}
[ -n "$scratch" ] || {
    echo "post-similar.sh: SCRATCH is not set" >&2
    exit 2
}
case $pr in
    *[!0-9]*)
        echo "post-similar.sh: $pr is not a pull request number" >&2
        exit 2
        ;;
esac
jq -se --argjson pr "$pr" 'length == 1 and (.[0]
    | type == "object" and .pr == $pr and (.sha | type == "string")
      and (.similar | type == "array")
      and all(.similar[]; (.kind | IN("pr", "issue")) and (.number | type == "number")
        and (.number as $n | .notice | type == "string"
          and test("(^|[^0-9A-Za-z])#" + ($n | tostring) + "([^0-9]|$)"))
        and (.kind == "issue" or (.other_sha | type == "string"))
        and (.kind == "issue" or .other_notice == null
          or (.other_notice | type == "string"
            and ((test("\\S") | not)
              or test("(^|[^0-9A-Za-z])#" + ($pr | tostring) + "([^0-9]|$)"))))))' \
    "$draft" > /dev/null 2>&1 || {
    echo "post-similar.sh: $draft is not a draft for #$pr whose entries are well formed and whose notices name the other thread as #N" >&2
    exit 2
}

# --check stops here: the shape is the whole question, so a caller can refuse a
# draft before it posts anything else.
if $check; then
    exit 0
fi
posted=$scratch/similar-posted
touch "$posted"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
sha=$(jq -r .sha "$draft")
bad=0

receipt()
{
    jq -nc --argjson pr "$pr" --argjson number "$1" --arg result "$2" --arg why "$3" \
        '{action: "similar", pr: $pr, number: $number, result: $result, why: $why}'
}

# links <view.json> <n>: whether the thread names #n.
links()
{
    jq -e --arg n "$2" '([.body] + [.comments[].body]) | join("\n")
        | test("(^|[^0-9A-Za-z])#" + $n + "([^0-9]|$)")' "$1" > /dev/null
}

# A view is read whole or not at all: a response without its comments is an
# unreadable thread, never one that links nothing.
view()
{
    gh pr view "$1" --repo "$repo" --json body,comments,headRefOid > "$2" \
        && jq -e '(.headRefOid | type == "string") and (.comments | type == "array")
            and all(.comments[]; .body | type == "string")' "$2" > /dev/null
}

# The head of PR $1 is still $2, asked again right before a post: a push during
# an earlier post would otherwise leave a notice about a diff nobody read.
still()
{
    [ "$(gh pr view "$1" --repo "$repo" --json headRefOid --jq .headRefOid)" = "$2" ]
}

# Every post is about two diffs, so both heads must still be the ones read.
heads_hold()
{
    still "$pr" "$sha" && {
        [ "$kind" != pr ] || still "$number" "$other_sha"
    }
}

# The claim this iteration holds, if any, and its release. A claim stays only
# for a pair that posted or is already linked, and those are exactly the pairs
# in the ledger, so every other way out of an iteration gives it back without a
# call of its own. A killed run never gets here, so its claim stays, which is
# the safe direction.
held=
release()
{
    [ -z "$held" ] || grep -qxF "$key" "$posted" || rmdir "$held"
    held=
}

i=0
total=$(jq '.similar | length' "$draft")
while [ "$i" -lt "$total" ]; do
    release
    entry=$(jq -c ".similar[$i]" "$draft")
    i=$((i + 1))
    kind=$(printf '%s\n' "$entry" | jq -r .kind)
    number=$(printf '%s\n' "$entry" | jq -r .number)

    # One finish at a time owns a pair, and it owns it before it reads either
    # thread: a read taken before the claim is stale once an earlier owner
    # failed and gave the claim back. The claim is a directory, since mkdir
    # either creates it or fails. A claim left by a killed run blocks the pair
    # for the rest of this sweep.
    if [ "$kind" = pr ]; then
        key=$(printf '%s\n' "$pr" "$number" | sort -n | paste -sd' ' -)
        if grep -qxF "$key" "$posted"; then
            receipt "$number" pair "posted earlier this sweep"
            continue
        fi
        claim=$posted.claim-$(printf '%s' "$key" | tr ' ' -)
        if ! mkdir "$claim" 2> /dev/null; then

            # Held is not posted: its owner may still fail, so this finish stays
            # pending and the next sweep looks again.
            receipt "$number" held "another finish holds this pair"
            bad=1
            continue
        fi
        held=$claim
    fi
    if ! view "$pr" "$work/this.json"; then
        receipt "$number" failed "#$pr could not be read"
        bad=1
        continue
    fi
    if [ "$(jq -r .headRefOid "$work/this.json")" != "$sha" ]; then
        receipt "$number" moved "#$pr moved after it was reviewed"
        bad=1
        continue
    fi

    # An issue has no thread of its own to mirror to, so only this side can be
    # owed.
    mine=false theirs=true
    links "$work/this.json" "$number" && mine=true
    if [ "$kind" = pr ]; then
        other_sha=$(printf '%s\n' "$entry" | jq -r .other_sha)
        if ! view "$number" "$work/other.json"; then
            receipt "$number" failed "#$number could not be read"
            bad=1
            continue
        fi
        if [ "$(jq -r .headRefOid "$work/other.json")" != "$other_sha" ]; then
            receipt "$number" moved "#$number moved after its diff was read"
            bad=1
            continue
        fi

        # Both threads are read before either post, since the first post is
        # itself a link and would always make the second look redundant.
        theirs=false
        links "$work/other.json" "$pr" && theirs=true
    fi
    if $mine && $theirs; then
        receipt "$number" linked "both threads already link each other"
        [ "$kind" != pr ] || echo "$key" >> "$posted"
        continue
    fi

    # A mirror owed to the other thread but not drafted fails before the first
    # post, not after it, so a rerun cannot repeat the notice.
    if [ "$kind" = pr ] && ! $theirs \
        && ! printf '%s\n' "$entry" | jq -e '.other_notice | strings | test("\\S")' > /dev/null; then
        receipt "$number" failed "the other thread needs an other_notice and the draft has none"
        bad=1
        continue
    fi

    ok=true
    moved=false
    if ! $mine; then
        if heads_hold; then
            printf '%s\n' "$entry" | jq -r .notice > "$work/notice.md"
            gh pr comment "$pr" --repo "$repo" --body-file "$work/notice.md" > /dev/null || ok=false
        else
            ok=false moved=true
        fi
    fi
    if $ok && ! $theirs && ! heads_hold; then
        ok=false moved=true
    fi
    if $ok && ! $theirs; then
        # A mirror owed is already known to be drafted, checked before any post.
        printf '%s\n' "$entry" | jq -r .other_notice > "$work/other.md"
        gh pr comment "$number" --repo "$repo" --body-file "$work/other.md" > /dev/null || ok=false
    fi
    if $ok; then
        receipt "$number" applied "notices posted"
        [ "$kind" != pr ] || echo "$key" >> "$posted"
    elif $moved; then
        receipt "$number" moved "a head changed during the posts; a rerun posts only what is missing"
        bad=1
    else
        receipt "$number" failed "a comment was refused; a rerun posts only what is missing"
        bad=1
    fi
done
release
exit "$bad"
