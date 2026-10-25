#!/bin/sh

# Validate the commit messages whose object IDs arrive on standard input.
#
# The shared body of the pre-push hook and of the pull-request commit-log step
# in .github/workflows/check.yml, so the rules live in scripts/git-commit-msg.sh
# alone and cannot drift into a second list that agrees with the first only by
# accident.

set -u

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
message_hook="$script_dir/git-commit-msg.sh"
failed=0

# Dependabot writes its own subjects and unwrapped link bodies, and nothing it
# offers makes either fit. Its commits are skipped by author, so anything a
# person adds to the same branch is still read.
dependabot='49699333+dependabot[bot]@users.noreply.github.com'

# --bot-only asks a narrower question of the same address: whether every commit
# on standard input is Dependabot's, with at least one to judge. The indent gate
# asks it before letting a pull request's formatting drift through as a report.
if [ "${1:-}" = --bot-only ]; then
    seen=0
    while read -r commit; do
        [ -n "$commit" ] || continue
        [ "$(git show -s --format=%ae "$commit")" = "$dependabot" ] || exit 1
        seen=1
    done
    [ "$seen" -eq 1 ]
    exit
fi

# The author's address and the message in one read, split here: a commit costs
# one git process rather than two, and a replay of a branch is mostly commits.
nl='
'

while read -r commit; do
    [ -n "$commit" ] || continue
    shown=$(git show -s --format='%ae%n%B' "$commit")
    [ "${shown%%"$nl"*}" = "$dependabot" ] && continue
    case $shown in
        *"$nl"*) message=${shown#*"$nl"} ;;
        *) message= ;;
    esac
    printf '%s\n' "$message" | "$message_hook" --stored - && continue

    # Through cat -v: a subject is text on its way to a terminal or a CI log,
    # and an escape sequence in one can rewrite what the reader sees around it.
    printf 'Invalid commit: %s\n' \
        "$(git show -s --format='%h %s' "$commit" | cat -v)" >&2
    failed=1
done

exit "$failed"
