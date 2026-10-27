//! The `tests` module of `src/web/recordings/workers.rs`, which declares this
//! file by path.
//! Everything here reaches into `src/web/recordings/workers.rs` through
//! `super`, so it is a unit
//! test and not an integration test: private items are in scope.

use super::*;

/// A public test-only RSA key shared with the delivery tests. The delivery
/// client parses the key material at construction, so asserting that a
/// configured deployment gets a client needs one that parses.
const TEST_ONLY_PRIVATE_KEY: &str = include_str!("../../test-only-private-key.pem");

/// A deployment with credentials gets a delivery client, and one without
/// gets a warning.
///
/// The `None` is a real state: the recording still runs and the sweeper
/// still resolves rows, but nothing delivers or deletes. That is worth a
/// warning and not a panic inside a router constructor, and both halves
/// were decided here with nothing asserting either.
#[test]
fn a_delivery_client_is_built_only_when_the_credentials_are_there() {
    let mut recording = crate::config::RecordingConfig {
        livekit: None,
        gcs_bucket: "codetrial-staging".to_string(),
        gcs_prefix: "codetrial".to_string(),
        drive_id: "0AKfixtureDriveId".to_string(),
        service_account_json: serde_json::json!({
            "type": "service_account",
            "client_email": "codetrial@example.iam.gserviceaccount.com",
            "private_key": TEST_ONLY_PRIVATE_KEY,
        })
        .to_string(),
        max_minutes: 45,
        bitrate: 2000,
        kill_switch: false,
        template_base_url: "https://recording.codetrial.example".to_string(),
    };
    assert!(
        delivery_provider(&recording).is_some(),
        "a service account with an address and a key builds a client"
    );

    recording.service_account_json = "{}".to_string();
    assert!(
        delivery_provider(&recording).is_none(),
        "a service account naming nobody delivers nothing"
    );
}

/// A pass that did nothing says nothing, and a pass that did says what.
///
/// Both loops run on a timer against whatever the database holds, so the
/// quiet case is the common one and logging it would bury the passes worth
/// reading. The counts are in the line because an operator reading it has
/// no other view of what the sweep moved.
#[test]
fn a_worker_pass_reports_only_when_it_moved_something() {
    use crate::recording::{DeliveryOutcome, SweepOutcome};

    assert_eq!(sweep_line(&SweepOutcome::default()), None);
    assert_eq!(delivery_line(&DeliveryOutcome::default()), None);

    let swept = SweepOutcome {
        retried: 1,
        deleted: 2,
        failed: 3,
    };
    assert_eq!(
        sweep_line(&swept).as_deref(),
        Some("recording sweep retried 1 deleted 2 failed 3")
    );

    let delivered = DeliveryOutcome {
        delivered: 4,
        retrying: 5,
        failed: 6,
    };
    assert_eq!(
        delivery_line(&delivered).as_deref(),
        Some("recording delivery delivered 4 retrying 5 failed 6")
    );

    // One field is enough to make a pass worth reporting, whichever it is.
    for outcome in [
        SweepOutcome {
            retried: 1,
            ..SweepOutcome::default()
        },
        SweepOutcome {
            deleted: 1,
            ..SweepOutcome::default()
        },
        SweepOutcome {
            failed: 1,
            ..SweepOutcome::default()
        },
    ] {
        assert!(sweep_line(&outcome).is_some(), "{outcome:?}");
    }
}

/// The guard exists so a sweeper cannot outlive the server that wanted it.
/// Nothing would prove that: `drop` could be empty and every other test
/// still passes, which is exactly the leak `BackgroundTasks` was added to
/// stop. It is the only test of that `Drop`: the quota refresher and the
/// session sweeper hold the same type and used to carry a copy of this test
/// each.
#[tokio::test]
async fn dropping_the_workers_aborts_the_tasks_they_own() {
    let mut workers = crate::web::BackgroundTasks::default();
    let watches: Vec<_> = (0..2)
        .map(|_| {
            // Never finishes on its own, so anything that ends it is the abort.
            let task = tokio::spawn(async {
                loop {
                    tokio::time::sleep(Duration::from_secs(3600)).await;
                }
            });
            let watch = task.abort_handle();
            workers.0.push(task);
            watch
        })
        .collect();
    assert!(
        watches.iter().all(|watch| !watch.is_finished()),
        "both tasks are running before the drop"
    );

    drop(workers);

    // The abort lands at each task's next scheduling point rather than inside
    // `drop`, so this yields rather than asserting straight away.
    for _ in 0..100 {
        if watches.iter().all(|watch| watch.is_finished()) {
            return;
        }
        tokio::task::yield_now().await;
    }
    panic!("a task outlived the BackgroundTasks that owned it");
}
