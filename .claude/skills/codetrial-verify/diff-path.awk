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

# The path on a diff --git line, the only one a section with no hunk (binary,
# mode only) may carry, with git's quotes dropped. A name holding " b/" makes
# the split ambiguous, but an unrenamed file names one path twice, so the
# halves settle it; a rename's "rename to" line says the rest.
function git_path(line,    p, n) {
    p = substr(line, 12)
    gsub(/"/, "", p)
    n = (length(p) - 1) / 2
    if (n == int(n) && substr(p, 3, n - 2) == substr(p, n + 4))
        return substr(p, n + 4)
    sub(/^a\/.* b\//, "", p)
    return p
}
