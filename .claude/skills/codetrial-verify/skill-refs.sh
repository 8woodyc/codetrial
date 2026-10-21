#!/bin/sh
# Report every reference in the skills that no longer resolves: relative links
# and their anchors, repo paths in backticks, make targets and CI lane names.
# One line per dangling reference; exits 1 when there is any.
set -u
cd "$(git rev-parse --show-toplevel)" || exit 2

# GitHub's heading anchor: lowercase, punctuation dropped, spaces to hyphens.
slugs()
{
    awk '/^```/ { f = !f; next } !f && sub(/^#+ /, "")' "$1" | tr '[:upper:]' '[:lower:]' \
        | sed 's/[^a-z0-9 _-]//g; s/ /-/g'
}

check()
{
    md=$1
    dir=$(dirname "$md")
    grep -oE '\]\([^):]+\)' "$md" | sed 's/^](//; s/)$//' \
        | while IFS= read -r link; do
            file=${link%%#*}
            target=$md
            [ -n "$file" ] && target=$dir/$file
            if [ ! -e "$target" ]; then
                echo "$md: link $link"
            elif [ "$file" != "$link" ] && ! slugs "$target" | grep -qxF "${link#*#}"; then
                echo "$md: anchor $link"
            fi
        done
    # shellcheck disable=SC2016
    grep -oE '`(\./)?(scripts|docs|src|web|tests|problem-bank|\.github|\.claude)/[^` ]*' "$md" \
        | sed 's/^`//; s|^\./||' | grep -v '[*<>{}$]' | sort -u \
        | while IFS= read -r path; do
            [ -e "$path" ] || echo "$md: path $path"
        done
    # shellcheck disable=SC2016
    grep -oE '`make [a-z-]+' "$md" | cut -c7- | sort -u \
        | while IFS= read -r target; do
            grep -q "^$target:" Makefile || echo "$md: make $target"
        done
    # shellcheck disable=SC2016
    grep -oE '`(\./)?scripts/[a-z0-9_.-]+ --[a-z-]+' "$md" | tr -d '`' \
        | sed 's|^\./||' | sort -u \
        | while read -r script flag; do
            grep -qF -- "$flag" "$script" 2> /dev/null \
                || echo "$md: flag $script $flag"
        done
    grep -oE 'gates \([a-z]+\)' "$md" | tr -d '()' | cut -d' ' -f2 | sort -u \
        | while IFS= read -r lane; do
            grep -qE "^ *- lane: $lane\$" .github/workflows/check.yml \
                || echo "$md: CI lane $lane"
        done
}

# The other direction: a check script outside the gate that no skill names.
unlisted()
{
    for script in scripts/*-check.sh; do
        grep -qF "/$script" scripts/test.sh && continue
        grep -qF "$script" .claude/skills/codetrial-verify/SKILL.md \
            || echo "codetrial-verify/SKILL.md: does not name $script"
    done
}

! {
    for md in .claude/skills/*/*.md; do check "$md"; done
    unlisted
} | grep .
