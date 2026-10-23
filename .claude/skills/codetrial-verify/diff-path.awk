# The path on a unified diff's --- or +++ line, shared by the scripts that walk
# `gh pr diff`, which prepend this file to their own awk program: drop the tab
# git appends after a name with a space, the quotes it puts around one with
# special characters, and the a/ or b/.
function header_path(line,    p) {
    p = substr(line, 5)
    sub(/\t.*$/, "", p)
    if (p ~ /^".*"$/) p = substr(p, 2, length(p) - 2)
    return p == "/dev/null" ? p : substr(p, 3)
}

# The new path on a diff --git line, the only one a section with no hunk
# (binary, renamed, mode only) may carry, with git's quotes dropped.
function git_path(line,    p) {
    p = line
    sub(/^diff --git "?a\/.* "?b\//, "", p)
    sub(/"$/, "", p)
    return p
}
