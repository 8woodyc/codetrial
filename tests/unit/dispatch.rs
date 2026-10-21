//! The `tests` module of `src/dispatch.rs`, which declares this file by path.
//! Everything here reaches into `src/dispatch.rs` through `super`, so it is a
//! unit
//! test and not an integration test: private items are in scope.

use super::*;

/// The capacity slot is released by dropping, so an interview that panics
/// gives its slot back like one that ends. A cap's worth of leaked slots
/// would refuse every interview after them for the life of the process.
#[tokio::test]
async fn a_panicking_interview_gives_its_capacity_back() {
    let state = Arc::new(Mutex::new(DispatchState::default()));
    state
        .lock()
        .unwrap()
        .live
        .insert("interview-boom".to_string(), true);
    let slot = Slot {
        state: Arc::clone(&state),
        guest: false,
        room_name: "interview-boom".to_string(),
    };

    let task = tokio::spawn(async move {
        let _held = slot;
        panic!("the interview blew up");
    });
    assert!(task.await.is_err(), "the task must have panicked");

    assert!(
        state.lock().unwrap().live.is_empty(),
        "a panicking interview must not keep its slot"
    );
}

/// The cap is the configured one, not a constant. An operator who set it to
/// one gets one, and the refusal names the number they chose.
#[tokio::test]
async fn the_configured_cap_is_the_one_enforced() {
    let state = Arc::new(Mutex::new(DispatchState::default()));
    state
        .lock()
        .unwrap()
        .live
        .insert("interview-first".to_string(), true);
    let dispatcher = LocalDispatcher {
        config: crate::config::load_from_pairs([
            ("LIVEKIT_URL", "wss://primary.example"),
            ("LIVEKIT_API_KEY", "primary-key"),
            ("LIVEKIT_API_SECRET", "primary-secret"),
            ("GOOGLE_API_KEY", "primary-google"),
        ])
        .unwrap(),
        runtime: tokio::runtime::Handle::current(),
        state: Arc::clone(&state),
        max_concurrent: 1,
    };
    let provider = Provider {
        id: crate::config::PRIMARY_PROVIDER_ID.to_string(),
        url: "wss://primary.example".to_string(),
        api_key: "primary-key".to_string(),
        api_secret: "primary-secret".to_string(),
        google_api_keys: Vec::new(),
    };

    assert!(
        dispatcher.ensure_agent("interview-second", &provider) == Err(DispatchRefusal::AtCapacity),
        "a second room must be refused at a cap of one"
    );

    // The room already running is still admitted, because a reload asks for the
    // same room and refusing it would break the page that is open.
    assert!(
        dispatcher
            .ensure_agent("interview-first", &provider)
            .is_ok()
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn a_concurrent_burst_never_overbooks_and_released_capacity_returns() {
    let state = Arc::new(Mutex::new(DispatchState::default()));
    let dispatcher = LocalDispatcher {
        config: crate::config::load_from_pairs([
            ("LIVEKIT_URL", "wss://primary.example"),
            ("LIVEKIT_API_KEY", "primary-key"),
            ("LIVEKIT_API_SECRET", "primary-secret"),
            ("GOOGLE_API_KEY", "primary-google"),
        ])
        .unwrap(),
        runtime: tokio::runtime::Handle::current(),
        state: Arc::clone(&state),
        max_concurrent: 8,
    };

    // Reserved from concurrent tasks released together, not in a loop: the cap
    // is a check and an insert under one lock, and a serial caller cannot tell
    // that apart from a version that overbooks when a hundred requests arrive
    // at once.
    let dispatcher = Arc::new(dispatcher);
    let gate = Arc::new(tokio::sync::Barrier::new(100));
    let mut tasks = Vec::new();
    for index in 0..100 {
        let dispatcher = Arc::clone(&dispatcher);
        let gate = Arc::clone(&gate);
        tasks.push(tokio::spawn(async move {
            gate.wait().await;
            match dispatcher.reserve_with_kind(&format!("interview-burst-{index}"), false) {
                Reservation::New(slot) => Some(slot),
                Reservation::Full { .. } => None,
                Reservation::GuestRoomInUse => {
                    panic!("signed-in reservations must not hit the guest room limit")
                }
                Reservation::Existing | Reservation::Finalizing => {
                    panic!("burst ids are unique")
                }
            }
        }));
    }
    let mut held = Vec::new();
    for task in tasks {
        held.extend(task.await.unwrap());
    }
    assert_eq!(held.len(), 8);
    assert_eq!(state.lock().unwrap().live.len(), 8);

    // Named from a slot that actually won: which eight of the hundred get in is
    // up to the scheduler, so the serial loop's assumption that it is the first
    // eight does not survive running them at once.
    let winner = held[0].room_name.clone();
    assert!(matches!(
        dispatcher.reserve_with_kind(&winner, false),
        Reservation::Existing
    ));
    held.truncate(3);
    assert_eq!(state.lock().unwrap().live.len(), 3);
    for index in 100..105 {
        assert!(matches!(
            dispatcher.reserve_with_kind(&format!("interview-burst-{index}"), false),
            Reservation::New(_)
        ));
    }
    // The temporary slots above drop at the end of each assertion.
    assert_eq!(state.lock().unwrap().live.len(), 3);
}

#[tokio::test]
async fn guest_capacity_is_limited_separately_and_released() {
    let state = Arc::new(Mutex::new(DispatchState::default()));
    let dispatcher = LocalDispatcher {
        config: crate::config::load_from_pairs([
            ("LIVEKIT_URL", "wss://primary.example"),
            ("LIVEKIT_API_KEY", "primary-key"),
            ("LIVEKIT_API_SECRET", "primary-secret"),
            ("GOOGLE_API_KEY", "primary-google"),
        ])
        .unwrap(),
        runtime: tokio::runtime::Handle::current(),
        state: Arc::clone(&state),
        max_concurrent: 8,
    };

    let first = dispatcher.reserve_with_kind("guest-one", true);
    let second = dispatcher.reserve_with_kind("guest-two", true);
    assert!(matches!(first, Reservation::New(_)));
    assert!(matches!(second, Reservation::New(_)));
    assert!(matches!(
        dispatcher.reserve_with_kind("guest-three", true),
        Reservation::Full {
            guest_cap: true,
            ..
        }
    ));

    // Signed-in interviews can still use the remaining global capacity.
    let signed_in = dispatcher.reserve_with_kind("signed-in-one", false);
    assert!(matches!(signed_in, Reservation::New(_)));
    drop(first);
    assert!(matches!(
        dispatcher.reserve_with_kind("guest-three", true),
        Reservation::New(_)
    ));
}

/// A provider's own Google key wins, over the global list as well as the
/// global key, and an empty one leaves the base credentials alone rather than
/// blanking them.
#[test]
fn provider_credentials_replace_the_base_without_blanking_the_key() {
    let mut base = crate::config::load_from_pairs([
        ("LIVEKIT_URL", "wss://primary.example"),
        ("LIVEKIT_API_KEY", "primary-key"),
        ("LIVEKIT_API_SECRET", "primary-secret"),
        ("GOOGLE_API_KEY", "primary-google"),
    ])
    .unwrap();
    let mut provider = Provider {
        id: "second".to_string(),
        url: "wss://second.example".to_string(),
        api_key: "second-key".to_string(),
        api_secret: "second-secret".to_string(),
        google_api_keys: Vec::new(),
    };

    let inherited = agent_config_for(&base, &provider);
    assert_eq!(inherited.livekit_url, "wss://second.example");
    assert_eq!(inherited.google_api_keys, ["primary-google"]);

    base.google_api_keys = vec!["global-first".into(), "global-second".into()];
    provider.google_api_keys = vec!["second-google".into()];
    let own_key = agent_config_for(&base, &provider);
    assert_eq!(own_key.google_api_keys, ["second-google"]);
    assert_eq!(
        crate::gemini::GeminiKeys::from_config(&own_key)
            .select()
            .unwrap(),
        "second-google"
    );
    provider.google_api_keys.clear();
    let inherited = agent_config_for(&base, &provider);
    assert_eq!(
        crate::gemini::GeminiKeys::from_config(&inherited)
            .select()
            .unwrap(),
        "global-first"
    );
    provider.google_api_keys = vec!["provider-first".into(), "provider-second".into()];
    let selected = agent_config_for(&base, &provider);
    assert_eq!(selected.google_api_keys, provider.google_api_keys);
    assert_eq!(selected.livekit_url, inherited.livekit_url);
    assert_eq!(selected.livekit_api_key, inherited.livekit_api_key);
    assert_eq!(selected.livekit_api_secret, inherited.livekit_api_secret);
}

#[tokio::test]
async fn guest_and_signed_in_interviews_cannot_share_a_live_room() {
    let state = Arc::new(Mutex::new(DispatchState::default()));
    let dispatcher = LocalDispatcher {
        config: crate::config::load_from_pairs([
            ("LIVEKIT_URL", "wss://primary.example"),
            ("LIVEKIT_API_KEY", "primary-key"),
            ("LIVEKIT_API_SECRET", "primary-secret"),
            ("GOOGLE_API_KEY", "primary-google"),
        ])
        .unwrap(),
        runtime: tokio::runtime::Handle::current(),
        state: Arc::clone(&state),
        max_concurrent: 4,
    };

    // A guest must not join a room already reserved by a signed-in interview.
    let signed_in_slot = match dispatcher.reserve_with_kind("shared-room", false) {
        Reservation::New(slot) => slot,
        _ => panic!("signed-in reservation should succeed"),
    };
    assert!(matches!(
        dispatcher.reserve_with_kind("shared-room", true),
        Reservation::GuestRoomInUse
    ));
    drop(signed_in_slot);

    // A signed-in user must not join a room reserved by a guest.
    let guest_slot = match dispatcher.reserve_with_kind("shared-room", true) {
        Reservation::New(slot) => slot,
        _ => panic!("guest reservation should succeed after release"),
    };
    assert!(matches!(
        dispatcher.reserve_with_kind("shared-room", false),
        Reservation::GuestRoomInUse
    ));
    drop(guest_slot);

    assert!(state.lock().unwrap().live.is_empty());
    assert!(state.lock().unwrap().guest_live.is_empty());
}

#[tokio::test]
async fn a_room_finalizing_its_report_cannot_be_reused_for_a_new_interview() {
    let state = Arc::new(Mutex::new(DispatchState::default()));
    let dispatcher = LocalDispatcher {
        config: crate::config::load_from_pairs([
            ("LIVEKIT_URL", "wss://primary.example"),
            ("LIVEKIT_API_KEY", "primary-key"),
            ("LIVEKIT_API_SECRET", "primary-secret"),
            ("GOOGLE_API_KEY", "primary-google"),
        ])
        .unwrap(),
        runtime: tokio::runtime::Handle::current(),
        state: Arc::clone(&state),
        max_concurrent: 2,
    };
    let Reservation::New(slot) = dispatcher.reserve_with_kind("local-room", false) else {
        panic!("fresh slot");
    };
    assert!(matches!(
        dispatcher.reserve_with_kind("local-room", false),
        Reservation::Existing
    ));
    slot.assessment_finished();
    assert!(matches!(
        dispatcher.reserve_with_kind("local-room", false),
        Reservation::Finalizing
    ));
    assert_eq!(state.lock().unwrap().live.len(), 1);
    // Other rooms can still use the unoccupied capacity.
    let Reservation::New(other) = dispatcher.reserve_with_kind("another-room", false) else {
        panic!("spare slot");
    };
    drop(slot);
    assert!(matches!(
        dispatcher.reserve_with_kind("local-room", false),
        Reservation::New(_)
    ));
    drop(other);
    assert!(state.lock().unwrap().live.is_empty());
}

#[tokio::test]
async fn released_guest_room_can_be_reserved_again_without_losing_its_marker() {
    let state = Arc::new(Mutex::new(DispatchState::default()));
    let dispatcher = LocalDispatcher {
        config: crate::config::load_from_pairs([
            ("LIVEKIT_URL", "wss://primary.example"),
            ("LIVEKIT_API_KEY", "primary-key"),
            ("LIVEKIT_API_SECRET", "primary-secret"),
            ("GOOGLE_API_KEY", "primary-google"),
        ])
        .unwrap(),
        runtime: tokio::runtime::Handle::current(),
        state: Arc::clone(&state),
        max_concurrent: 4,
    };

    let Reservation::New(first_slot) = dispatcher.reserve_with_kind("guest-reused-room", true)
    else {
        panic!("the first guest reservation should succeed");
    };
    drop(first_slot);

    let Reservation::New(second_slot) = dispatcher.reserve_with_kind("guest-reused-room", true)
    else {
        panic!("the released guest room should be reservable again");
    };

    {
        let state = state.lock().unwrap();
        assert!(state.live.contains_key("guest-reused-room"));
        assert!(state.guest_live.contains("guest-reused-room"));
    }

    assert!(matches!(
        dispatcher.reserve_with_kind("guest-reused-room", false),
        Reservation::GuestRoomInUse
    ));

    drop(second_slot);

    let state = state.lock().unwrap();
    assert!(!state.live.contains_key("guest-reused-room"));
    assert!(!state.guest_live.contains("guest-reused-room"));
}

#[tokio::test]
async fn local_dispatcher_enforces_guest_admission_capacity() {
    let state = Arc::new(Mutex::new(DispatchState::default()));
    let config = crate::config::load_from_pairs([
        ("LIVEKIT_URL", "wss://primary.example"),
        ("LIVEKIT_API_KEY", "primary-key"),
        ("LIVEKIT_API_SECRET", "primary-secret"),
        ("GOOGLE_API_KEY", "primary-google"),
    ])
    .unwrap();

    let dispatcher = LocalDispatcher {
        config,
        runtime: tokio::runtime::Handle::current(),
        state: Arc::clone(&state),
        max_concurrent: 8,
    };

    assert!(dispatcher.supports_guest_admission());

    let first = dispatcher.reserve_with_kind("guest-capacity-one", true);
    let second = dispatcher.reserve_with_kind("guest-capacity-two", true);

    assert!(matches!(first, Reservation::New(_)));
    assert!(matches!(second, Reservation::New(_)));

    let provider = Provider {
        id: crate::config::PRIMARY_PROVIDER_ID.to_string(),
        url: "wss://primary.example".to_string(),
        api_key: "primary-key".to_string(),
        api_secret: "primary-secret".to_string(),
        google_api_keys: vec!["primary-google".to_string()],
    };

    assert_eq!(
        RoomDispatcher::ensure_guest_agent(&dispatcher, "guest-capacity-three", &provider,),
        Err(DispatchRefusal::AtCapacity),
    );
}
