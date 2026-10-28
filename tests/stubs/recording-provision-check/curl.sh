#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_ROOT/curl.log"

# --output is read out of the arguments rather than assumed, because the
# template probe writes the body to a file and puts only the status on stdout.
out=/dev/null
previous=
credentialed=no
for argument in "$@"; do
    [ "$previous" != --output ] || out=$argument
    [ "$argument" != --config ] || credentialed=yes
    previous=$argument
done

# A Google API call with no credential is a 401, not a silent success. Without
# this the audit could be made to run unauthenticated and every test still
# passed.
unauthenticated()
{
    [ "$credentialed" = no ] || return 0
    printf '%s' '{"error":{"code":401,"message":"Login Required"}}'
    exit 22
}

fails()
{
    [ "$FAKE_FAIL" = "$1" ] || return 0
    echo "simulated failure: $1" >&2
    exit 22
}

# Answer a Drive listing the way the API answers one: through the request's own
# `fields` mask, withholding every field the request did not name, at every
# level of the mask. Serving the whole fixture regardless was how dropping
# `permissionDetails` from a query stayed invisible to the whole suite, and it
# is not a property one field can be spot-checked for: the earlier version
# string-matched `deleted,permissionDetails(permissionType,role,inherited))`,
# which passed for the wrong reason if the mask were reordered and said nothing
# at all about `nextPageToken`. Withholding by the mask covers the nesting bug
# it was written for -- a sibling `permissions(...),permissionDetails(...)`
# spelling withholds `permissionDetails` from every permission -- and covers the
# fields nothing had spoken for.
serve()
{
    answer=$1
    shift
    python3 - "$answer" "$@" << 'PROJECT'
import json
import sys
import urllib.parse

answer_path, *arguments = sys.argv[1:]
url = next(argument for argument in arguments if "://" in argument and "?" in argument)
query = urllib.parse.parse_qs(url.split("?", 1)[1])

# A shared-drive file is not visible to a request that has not opted in, and
# the real API answers 404 rather than serving it. Without this the flag could
# be dropped from either query and every test still passed.
if query.get("supportsAllDrives") != ["true"]:
    print(json.dumps({"error": {"code": 404, "message": "File not found"}}))
    raise SystemExit(22)


# A Drive fields mask as {name: submask or None}, and where it ended.
def parse_mask(text, start=0):
    fields, name, index = {}, "", start
    while index < len(text):
        character = text[index]
        index += 1
        if character == "(":
            fields[name], index = parse_mask(text, index)
            name = ""
        elif character == ")":
            break
        elif character == ",":
            if name:
                fields[name] = None
            name = ""
        else:
            name += character
    if name:
        fields[name] = None
    return fields, index


def project(value, mask):
    if mask is None:
        return value
    if isinstance(value, list):
        return [project(item, mask) for item in value]
    if isinstance(value, dict):
        return {
            key: project(item, mask[key]) for key, item in value.items() if key in mask
        }
    return value


mask, _ = parse_mask(query["fields"][0])
print(json.dumps(project(json.load(open(answer_path, encoding="utf-8")), mask)))
PROJECT
}

case "$*" in
    *"/files/$FAKE_DRIVE_ID/permissions?"*pageToken=*)
        unauthenticated
        fails drive-permissions
        serve "$FAKE_ROOT/drive-page-2.json" "$@"
        ;;
    *"/files/$FAKE_DRIVE_ID/permissions?"*)
        unauthenticated
        fails drive-permissions
        serve "$FAKE_ROOT/drive.json" "$@"
        ;;
    *"/drive/v3/files?"*pageToken=*)
        unauthenticated
        fails drive-files
        serve "$FAKE_ROOT/drive-files-page-2.json" "$@"
        ;;
    *"/drive/v3/files?"*)
        unauthenticated
        fails drive-files
        serve "$FAKE_ROOT/drive-files.json" "$@"
        ;;
    *"/drive/v3/files/"*"/permissions?"*)
        unauthenticated
        fails file-permissions

        # Per-file answers, so one run can hold a folder and the file beneath it
        # and show that the folder's own grant is refused where the child's
        # inherited copy of it is skipped. The shared fixture is the fallback,
        # because almost every test varies one drive-wide shape rather than the
        # relationship between two files.
        file=
        for argument in "$@"; do
            case $argument in
                *"/drive/v3/files/"*"/permissions?"*)
                    file=${argument#*"/drive/v3/files/"}
                    file=${file%%/permissions*}
                    ;;
            esac
        done
        answer=$FAKE_ROOT/file-permissions-$file.json
        [ -f "$answer" ] || answer=$FAKE_ROOT/file-permissions.json
        serve "$answer" "$@"
        ;;
    *"/recording/index.html"*)
        printf '%s\n' "$*" >> "$FAKE_ROOT/curl-args.log"
        printf '%s' "$FAKE_TEMPLATE_BODY" > "$out"
        printf '%s' "$FAKE_TEMPLATE_STATUS"
        [ "$FAKE_TEMPLATE_TRANSFER" = complete ] || exit 28
        ;;
    *)
        echo "unexpected curl invocation: $*" >&2
        exit 99
        ;;
esac
