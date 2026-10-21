//! Synthetic report probes, not calibration or hiring-quality evidence.
//! Credential-free corpus and metric checks: cargo test --lib report_eval.

use super::*;
use serde::Deserialize;
use std::collections::{BTreeMap, BTreeSet};

const MANIFEST: &str = include_str!("../fixtures/report-eval/manifest.json");
const FAMILIES: [&str; 6] = [
    "repeat",
    "equivalent",
    "semantic",
    "identity_swap",
    "asr",
    "phase_label",
];
const DISCLAIMER: &str = "synthetic stability probe; NOT CALIBRATED; not a hiring-quality, fairness or correctness measure";

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    version: u32,
    synthetic: bool,
    items: Vec<Item>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Item {
    id: String,
    provenance: String,
    input: Value,
    phase_labels: Vec<PhaseLabel>,
    variants: Vec<Variant>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PhaseLabel {
    phase: String,
    opportunity: bool,
    answer: bool,
    assessable: bool,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Variant {
    id: String,
    family: String,
    patch: BTreeMap<String, Value>,
    expectation: String,
}

fn manifest() -> Manifest {
    serde_json::from_str(MANIFEST).expect("valid synthetic manifest")
}

fn variant_input(item: &Item, variant: &Variant) -> Value {
    let mut input = item.input.clone();
    for (key, value) in &variant.patch {
        assert!(input.get(key).is_some(), "unknown patch field {key}");
        input[key] = value.clone();
    }
    input
}

fn prompt(input: &Value) -> String {
    crate::agent::report_prompt(crate::agent::ReportPromptInput {
        problem: crate::agent::get_problem(input["problem"].as_str()),
        interview_mode: InterviewMode::Coding,
        board_attached: false,
        transcript: input["transcript"].as_str().unwrap(),
        rolling_assessment: input["rolling_assessment"].as_str().unwrap(),
        final_code: input["final_code"].as_str().unwrap(),
        language: input["language"].as_str().unwrap(),
        hints_used: input["hints_used"].as_u64().unwrap().try_into().unwrap(),
        hint_rung: input["hint_rung"].as_u64().unwrap().try_into().unwrap(),
        volunteered_hints: input["volunteered_hints"]
            .as_u64()
            .unwrap()
            .try_into()
            .unwrap(),
        duration_min: input["duration_min"].as_u64().unwrap().try_into().unwrap(),
        elapsed_min: input["elapsed_min"].as_f64().unwrap(),
        test_summary: input["test_summary"].as_str().unwrap(),
        practice_level: input["practice_level"].as_str(),
        evidence: input["evidence"].as_str().unwrap(),
        behavioral_round: if input["behavioral_opened"].as_bool().unwrap() {
            crate::agent::BehavioralRound::Opened
        } else {
            crate::agent::BehavioralRound::NeverOpened
        },
        follow_ups_released: input["follow_ups_released"].as_bool().unwrap(),
    })
}

// Band edges are the report rubric in src/agent/prompts.rs.
fn band(score: u64) -> usize {
    assert!(score <= 100);
    [40, 60, 75, 90]
        .iter()
        .filter(|&&edge| score >= edge)
        .count()
}

fn spread(scores: &[u64]) -> Option<u64> {
    Some(scores.iter().max()? - scores.iter().min()?)
}

fn mean(scores: &[u64]) -> Option<f64> {
    (!scores.is_empty()).then(|| scores.iter().sum::<u64>() as f64 / scores.len() as f64)
}

// The expected range grows with sample size, so comparing spreads of
// differently sized sets reads as name sensitivity from noise alone. The mean
// shift is expected to be zero when the swapped name has no effect; the repeat
// spread beside it gives the noise to read it against.
fn mean_shift(repeat: &[u64], identity: &[u64]) -> Option<f64> {
    Some(mean(identity)? - mean(repeat)?)
}

fn rate(values: impl IntoIterator<Item = bool>) -> Option<f64> {
    let (yes, count) = values.into_iter().fold((0, 0), |(yes, count), value| {
        (yes + usize::from(value), count + 1)
    });
    (count > 0).then(|| yes as f64 / count as f64)
}

fn paired_metrics(base: &Value, other: &Value) -> Value {
    let a = base["frameworkAssessment"]["phases"].as_array().unwrap();
    let b = other["frameworkAssessment"]["phases"].as_array().unwrap();
    let pairs = a
        .iter()
        .map(|row| {
            let peer = b.iter().find(|peer| peer["phase"] == row["phase"]).unwrap();
            (row["score"].as_u64(), peer["score"].as_u64())
        })
        .collect::<Vec<_>>();
    json!({
        "phase_comparisons": pairs.len(),
        "scored_pairs": pairs.iter().filter(|(a,b)|a.is_some() && b.is_some()).count(),
        "same_band_rate": rate(pairs.iter().filter_map(|(a,b)| Some(band((*a)?) == band((*b)?)))),
        "assessability_agreement": rate(pairs.iter().map(|(a,b)| a.is_some() == b.is_some())),
        "verdict_flip": base["decision"] != other["decision"],
    })
}

fn label_agreement(item: &Item, report: &Value) -> Option<f64> {
    rate(item.phase_labels.iter().map(|label| {
        let row = report["frameworkAssessment"]["phases"]
            .as_array()
            .unwrap()
            .iter()
            .find(|row| row["phase"] == label.phase)
            .unwrap();
        row["score"].is_number() == label.assessable
    }))
}

#[test]
fn report_eval_manifest_covers_seven_synthetic_items_and_all_families() {
    let corpus = manifest();
    assert_eq!(corpus.version, 1);
    assert!(corpus.synthetic);
    assert_eq!(corpus.items.len(), 7);
    let mut ids = BTreeSet::new();
    for item in &corpus.items {
        assert!(ids.insert(&item.id));
        assert!(!item.provenance.is_empty());
        assert_eq!(item.input["interview_mode"], "coding");
        assert_eq!(item.input["board_attached"], false);
        assert!(item.variants.len() >= 12);
        let families = item
            .variants
            .iter()
            .map(|v| v.family.as_str())
            .collect::<BTreeSet<_>>();
        assert!(BTreeSet::from(FAMILIES).is_subset(&families));
        let mut variants = BTreeSet::new();
        for variant in &item.variants {
            assert!(variants.insert(&variant.id));
            assert!(!variant.expectation.is_empty());
            let input = variant_input(item, variant);
            assert!(!prompt(&input).is_empty());
            if matches!(variant.family.as_str(), "repeat" | "phase_label") {
                assert_eq!(input, item.input);
            } else {
                assert_ne!(input, item.input, "no-op variant {}", variant.id);
            }
            if variant.family == "identity_swap" {
                assert_eq!(variant.patch.keys().collect::<Vec<_>>(), vec!["transcript"]);
                assert_ne!(input["transcript"], item.input["transcript"]);
            }
        }
        let mut labelled = BTreeSet::new();
        for label in &item.phase_labels {
            assert!(labelled.insert(&label.phase));
            assert!(
                [
                    "Repeat",
                    "Example",
                    "Algorithm",
                    "Coding",
                    "Test",
                    "Optimizations",
                    "Situation",
                    "Task",
                    "Action",
                    "Result"
                ]
                .contains(&label.phase.as_str())
            );
            assert!(!label.answer || label.opportunity);
            assert!(!label.assessable || label.opportunity);
        }
    }
    assert_eq!(
        DISCLAIMER,
        "synthetic stability probe; NOT CALIBRATED; not a hiring-quality, fairness or correctness measure"
    );
}

#[test]
fn report_eval_metrics_have_hand_computed_denominators_and_nulls() {
    assert_eq!(spread(&[]), None);
    assert_eq!(spread(&[70, 90, 80]), Some(20));
    assert_eq!(spread(&[70]), Some(0));
    assert_eq!(rate([]), None);
    assert_eq!(rate([true, false, true, false]), Some(0.5));
    assert_eq!(
        [39, 40, 59, 60, 74, 75, 89, 90, 100].map(band),
        [0, 1, 1, 2, 2, 3, 3, 4, 4]
    );
    let a = json!({"decision":"HIRE","frameworkAssessment":{"phases":[{"phase":"A","score":70},{"phase":"B","score":null},{"phase":"C","score":90}]}});
    let b = json!({"decision":"NO_HIRE","frameworkAssessment":{"phases":[{"phase":"C","score":89},{"phase":"A","score":74},{"phase":"B","score":50}]}});
    let metrics = paired_metrics(&a, &b);
    assert_eq!(metrics["phase_comparisons"], 3);
    assert_eq!(metrics["scored_pairs"], 2);
    assert_eq!(metrics["same_band_rate"], 0.5);
    assert_eq!(metrics["assessability_agreement"], 2.0 / 3.0);
    assert_eq!(metrics["verdict_flip"], true);
    let corpus = manifest();
    let item = &corpus.items[1];
    let report = json!({"frameworkAssessment":{"phases":[{"phase":"Situation","score":null},{"phase":"Task","score":null},{"phase":"Action","score":50},{"phase":"Result","score":null}]}});
    let mut report = report;
    for phase in [
        "Repeat",
        "Example",
        "Algorithm",
        "Coding",
        "Test",
        "Optimizations",
    ] {
        report["frameworkAssessment"]["phases"]
            .as_array_mut()
            .unwrap()
            .push(json!({"phase":phase,"score":75}));
    }
    assert_eq!(label_agreement(item, &report), Some(0.8));
    assert_eq!(mean(&[]), None);
    assert_eq!(mean_shift(&[70, 72], &[75]), Some(4.0));
    assert_eq!(mean_shift(&[70, 72, 74], &[72, 72, 72]), Some(0.0));
    assert_eq!(mean_shift(&[], &[70]), None);
    assert_eq!(mean_shift(&[70], &[]), None);
}

#[derive(Debug, Default, PartialEq)]
struct Ledger {
    calls: u64,
    tokens: u64,
    pending: bool,
    stopped: bool,
}

impl Ledger {
    fn permits(&self, call_cap: u64, token_cap: u64) -> bool {
        !self.pending && !self.stopped && self.calls < call_cap && self.tokens < token_cap
    }

    fn apply(&mut self, event: &Value, caps: (u64, u64)) -> Result<(), String> {
        match event["event"].as_str() {
            Some("reserved") if self.permits(caps.0, caps.1) => {
                self.calls = self.calls.checked_add(1).ok_or("call overflow")?;
                self.pending = true;
            }
            Some("observed") if self.pending => {
                self.pending = false;
                match event["total_tokens"].as_u64() {
                    Some(tokens) => {
                        self.tokens = self.tokens.checked_add(tokens).ok_or("token overflow")?
                    }
                    None => self.stopped = true,
                }
                if event["failed"].as_bool().ok_or("missing failed boolean")? {
                    self.stopped = true;
                }
            }
            Some("reserved" | "observed") => return Err("invalid attempt sequence".into()),
            _ => return Err("unknown attempt event".into()),
        }
        Ok(())
    }
}

#[test]
fn report_eval_budget_fold_preserves_caps_and_unknown_usage() {
    let mut ledger = Ledger::default();
    assert!(!ledger.permits(0, 100));
    assert!(!ledger.permits(3, 0));
    for tokens in [9, 1] {
        assert!(ledger.permits(2, 10));
        ledger
            .apply(&json!({"event":"reserved"}), (100, 100))
            .unwrap();
        assert!(!ledger.permits(2, 10));
        ledger
            .apply(
                &json!({"event":"observed","total_tokens":tokens,"failed":false}),
                (100, 100),
            )
            .unwrap();
    }
    assert_eq!(ledger.calls, 2);
    assert_eq!(ledger.tokens, 10);
    assert!(!ledger.permits(2, 10));
    assert!(!ledger.permits(3, 10));
    assert!(ledger.permits(3, 11));
    ledger
        .apply(&json!({"event":"reserved"}), (100, 100))
        .unwrap();
    ledger
        .apply(
            &json!({"event":"observed","total_tokens":null,"failed":true}),
            (100, 100),
        )
        .unwrap();
    assert!(ledger.stopped);
    assert_eq!(ledger.tokens, 10);
    assert!(!ledger.permits(100, 100));
    assert!(
        ledger
            .apply(&json!({"event":"reserved"}), (100, 100))
            .is_err()
    );
    let mut orphan = Ledger::default();
    orphan
        .apply(&json!({"event":"reserved"}), (100, 100))
        .unwrap();
    assert!(!orphan.permits(100, 100));
    assert!(
        orphan
            .apply(&json!({"event":"reserved"}), (100, 100))
            .is_err()
    );
    let mut overshoot = Ledger::default();
    overshoot
        .apply(&json!({"event":"reserved"}), (100, 100))
        .unwrap();
    overshoot
        .apply(
            &json!({"event":"observed","total_tokens":11,"failed":false}),
            (100, 100),
        )
        .unwrap();
    assert!(!overshoot.permits(2, 10));
    assert!(
        overshoot
            .apply(&json!({"event":"reserved"}), (2, 10))
            .is_err()
    );
    let mut malformed = Ledger::default();
    malformed
        .apply(&json!({"event":"reserved"}), (1, 10))
        .unwrap();
    assert!(
        malformed
            .apply(&json!({"event":"observed","total_tokens":1}), (1, 10))
            .is_err()
    );
}
