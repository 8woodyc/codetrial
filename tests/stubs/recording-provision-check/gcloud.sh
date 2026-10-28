#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_ROOT/gcloud.log"

# Which account the last activation selected. The script's stated rule is that
# policy reads use the independent auditor and access proofs use the delivery
# account, and nothing observed either half before.
active=
[ ! -f "$FAKE_ROOT/active" ] || active=$(cat "$FAKE_ROOT/active")

need_active()
{
    [ "$active" = "$1" ] || {
        echo "PERMISSION_DENIED: $2 as ${active:-no account}, expected $1" >&2
        exit 1
    }
}

fails()
{
    [ "$FAKE_FAIL" = "$1" ] || return 0
    echo "simulated failure: $1" >&2
    exit 1
}

case "$*" in
    *"auth activate-service-account"*)
        case "$*" in
            *iam-auditor.json*) printf 'auditor\n' > "$FAKE_ROOT/active" ;;
            *delivery-service-account.json*) printf 'delivery\n' > "$FAKE_ROOT/active" ;;
            *)
                echo "activated an unknown key: $*" >&2
                exit 99
                ;;
        esac
        ;;
    *"auth print-access-token"*)
        fails print-access-token
        printf '%s\n' "$FAKE_TOKEN"
        ;;
    *"storage buckets get-iam-policy"*)
        need_active auditor "read the bucket IAM policy"
        fails bucket-iam
        cat "$FAKE_ROOT/bucket-iam.json"
        ;;
    *"projects describe"*)
        need_active auditor "describe the project"
        fails project-describe
        printf '%s\n' "$FAKE_PROJECT_NUMBER"
        ;;
    *"projects get-ancestors-iam-policy"*)
        need_active auditor "read ancestor IAM"
        fails ancestors-iam
        cat "$FAKE_ROOT/ancestors-iam.json"
        ;;
    *"storage buckets describe"*--raw* | *--raw*"storage buckets describe"*)
        need_active auditor "describe the bucket"
        fails bucket-describe
        cat "$FAKE_ROOT/bucket.json"
        ;;
    *"storage buckets describe"*)
        need_active auditor "describe the bucket"
        cat "$FAKE_ROOT/bucket-standardised.json"
        ;;
    *"storage ls"*)
        need_active delivery "list the bucket"
        fails storage-ls
        ;;
    *"iam service-accounts get-iam-policy"*)
        need_active auditor "read the delivery account's own IAM policy"
        fails delivery-account-iam
        cat "$FAKE_ROOT/delivery-account-iam.json"
        ;;
    *)
        echo "unexpected gcloud invocation: $*" >&2
        exit 99
        ;;
esac
