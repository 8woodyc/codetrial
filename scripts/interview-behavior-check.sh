#!/bin/sh
set -eu

# The interviewer behaviour check. Outside the gate: it makes Gemini requests.
#
# The judgement lives in `tests/interview_behavior.rs`, the report check in
# `tests/unit/gemini.rs`, and both read the key the way the binary does:
# GOOGLE_API_KEY from the config file `scripts/gemini-check.sh` names, over the
# environment. This script only names that file.
#
#   BEHAVIOR_PROBLEMS=3sum,coin-change   which problems to script (default three)
#   GEMINI_BEHAVIOR_MODEL=...            text model standing in for the live one
#   BEHAVIOR_CHECK_MODE=injection        only the bounded injection text proxy
#
# Injection mode requires BEHAVIOR_INJECTION_CONFIG (isolated config),
# BEHAVIOR_INJECTION_RESULTS (new scratch JSONL path), and explicit positive
# BEHAVIOR_INJECTION_CALL_CAP / BEHAVIOR_INJECTION_TOKEN_CAP. No key rotation,
# retries or default-config fallback. Token limits can overshoot by one call. It
# retains every request, tool dispatch and case outcome, including failures.
# Unknown usage or transport errors stop spending; results cannot be resumed.
# Report injection pairs use report_eval_run separately; see
# tests/fixtures/report-eval/README.md.

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

# Cargo runs the tests from the package root, not from where this was invoked,
# so a relative path is resolved here first.
absolute()
{
    case $1 in
        /*) printf '%s' "$1" ;;
        *) printf '%s' "$PWD/$1" ;;
    esac
}
case ${BEHAVIOR_CHECK_MODE:-default} in
    injection)
        # Unset stays unset, so the probe names the missing variable.
        if [ -n "${BEHAVIOR_INJECTION_CONFIG:-}" ]; then
            BEHAVIOR_INJECTION_CONFIG=$(absolute "$BEHAVIOR_INJECTION_CONFIG")
            export BEHAVIOR_INJECTION_CONFIG
        fi
        if [ -n "${BEHAVIOR_INJECTION_RESULTS:-}" ]; then
            BEHAVIOR_INJECTION_RESULTS=$(absolute "$BEHAVIOR_INJECTION_RESULTS")
            export BEHAVIOR_INJECTION_RESULTS
        fi
        exec cargo test --manifest-path "$ROOT/Cargo.toml" --test interview_behavior \
            injection::instruction_injection_probes -- --ignored --exact --nocapture
        ;;
    default) ;;
    *)
        echo 'BEHAVIOR_CHECK_MODE must be default or injection' >&2
        exit 2
        ;;
esac
CODETRIAL_ENV=$(absolute "${CODETRIAL_ENV:-$ROOT/config/codetrial.env.local}")
export CODETRIAL_ENV

cargo test --manifest-path "$ROOT/Cargo.toml" --test interview_behavior -- \
    --ignored --nocapture --skip injection::instruction_injection_probes

# The report check lives with the crate's own tests so it can run the report
# path production runs, repairs included, without publishing it.
cargo test --manifest-path "$ROOT/Cargo.toml" --lib \
    a_misrecognized_turn_neither_appears_in_nor_decides_the_report -- \
    --ignored --nocapture
