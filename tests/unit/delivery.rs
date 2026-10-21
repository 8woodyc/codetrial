//! The `tests` module of `src/delivery.rs`, which declares this file by path.
//! Everything here reaches into `src/delivery.rs` through `super`, so it is a
//! unit
//! test and not an integration test: private items are in scope.

use super::*;

#[test]
fn dates_are_rfc_3339_in_utc() {
    // The shape Drive wants, and the arithmetic that produces it, checked
    // against dates a leap year and a century get wrong when it is written by
    // hand.
    assert_eq!(rfc3339(0), "1970-01-01T00:00:00Z");
    assert_eq!(rfc3339(1_771_632_000), "2026-02-21T00:00:00Z");
    assert_eq!(
        rfc3339(1_771_632_000 + 24 * 60 * 60),
        "2026-02-22T00:00:00Z"
    );
    // 2000 was a leap year and 1900 was not; the day after 2000-02-28.
    assert_eq!(rfc3339(951_782_400), "2000-02-29T00:00:00Z");
    assert_eq!(rfc3339(1_767_225_599), "2025-12-31T23:59:59Z");
}

#[test]
fn object_names_survive_their_slashes() {
    // The JSON API takes the whole object name as one path segment, so a prefix
    // that arrived unescaped would name a different object, or none.
    assert_eq!(
        percent_encode_component("codetrial/abc123.mp4"),
        "codetrial%2Fabc123.mp4"
    );
    assert_eq!(percent_encode_component("a b"), "a%20b");
    assert_eq!(
        percent_encode_component("plain-name_1.mp4"),
        "plain-name_1.mp4"
    );
}

#[test]
fn a_deletion_that_finds_nothing_succeeded() {
    assert!(gone_or_ok(204, "x").is_ok());
    assert!(
        gone_or_ok(404, "x").is_ok(),
        "retention runs more than once"
    );
    assert!(gone_or_ok(403, "x").is_err());
}

/// `bytes=0-n`, where `n` is the last byte the session holds.
fn with_range(range: Option<&str>) -> reqwest::Response {
    let mut builder = axum::http::Response::builder().status(308);
    if let Some(range) = range {
        builder = builder.header(axum::http::header::RANGE, range);
    }
    reqwest::Response::from(builder.body(String::new()).unwrap())
}

/// The offset a `308` moves the upload to. Everything here is one
/// subtraction away from writing a file with a hole in it: the loop trusts
/// this number and never re-sends what it says arrived.
#[test]
fn a_resume_offset_is_believed_only_when_it_can_be_read_exactly() {
    // A session that has nothing yet sends no header at all, which is not an
    // error and must not be read as one.
    assert_eq!(stored_bytes(&with_range(None), 1_000).unwrap(), 0);

    // Inclusive end, so the count is one more than the index. Off by one here
    // re-sends a byte or skips one, and only the second is visible.
    assert_eq!(
        stored_bytes(&with_range(Some("bytes=0-0")), 1_000).unwrap(),
        1
    );
    assert_eq!(
        stored_bytes(&with_range(Some("bytes=0-999")), 1_000).unwrap(),
        1_000
    );
    assert_eq!(
        stored_bytes(&with_range(Some("bytes=0-42 ")), 1_000).unwrap(),
        43,
        "a trailing space is still a readable range"
    );

    // Anything that is not `bytes=0-n` is a header this code cannot act on.
    // Guessing at one moves the offset past bytes that were never stored, so
    // each of these has to be an error and not a zero.
    for range in [
        "bytes=500-999",
        "bytes=0-abc",
        "bytes=0-",
        "0-999",
        "",
        "bytes=0--1",
    ] {
        assert!(
            stored_bytes(&with_range(Some(range)), 1_000).is_err(),
            "{range:?} must not be guessed at"
        );
    }

    // `end + 1` on the largest value a range can name. Wrapping it would report
    // an empty session and restart a finished upload from zero.
    assert!(
        stored_bytes(&with_range(Some(&format!("bytes=0-{}", u64::MAX))), 1_000).is_err(),
        "an offset that overflows must be refused"
    );

    // More than the object has means the session is not the one this transfer
    // opened. Believing it finishes a truncated file.
    assert!(
        stored_bytes(&with_range(Some("bytes=0-1000")), 1_000).is_err(),
        "a session cannot hold more than the object has"
    );
}

#[test]
fn a_service_account_without_a_key_is_refused_at_startup() {
    let now = Arc::new(|| 0);
    assert!(
        GoogleDelivery::new("{}", "bucket", "drive", now.clone()).is_err(),
        "a credential this broken must stop a deployment, not an interview"
    );
    assert!(GoogleDelivery::new("not json", "bucket", "drive", now.clone()).is_err());

    // Base64 that decodes and is not a key. Without the `ring` check this was
    // accepted at startup and failed at the first delivery, which is the
    // failure the doc comment above `new` promises not to have.
    let pretend = serde_json::json!({
        "client_email": "codetrial@example.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nbm90IGEga2V5\n-----END PRIVATE KEY-----\n",
    })
    .to_string();
    assert!(GoogleDelivery::new(&pretend, "bucket", "drive", now).is_err());
}

#[test]
fn service_account_validation_agrees_at_configuration_and_startup() {
    for field in ["client_email", "private_key"] {
        for value in [
            serde_json::Value::Null,
            json!(false),
            json!(""),
            json!(" \t\n"),
        ] {
            let mut credentials =
                json!({"client_email": "delivery@example.com", "private_key": "key"});
            credentials[field] = value;
            let credentials = credentials.to_string();
            let expected = format!("the service account has no {field}");
            assert_eq!(
                readable_service_account(&credentials),
                Err(expected.clone())
            );
            assert_eq!(
                GoogleDelivery::new(&credentials, "bucket", "drive", Arc::new(|| 0)).err(),
                Some(expected)
            );
        }
    }
}

/// A delivery whose staged-object reads and Drive calls both go to `base`.
/// Nothing here signs, so the credential only has to exist.
fn delivery_reading_from(base: String) -> GoogleDelivery {
    GoogleDelivery {
        http: crate::http_client_builder().no_proxy().build().unwrap(),
        credentials: ServiceAccount {
            client_email: String::new(),
            private_key: Vec::new(),
            token_uri: String::new(),
        },
        bucket: "staging".to_string(),
        drive_id: "drive".to_string(),
        token: Mutex::new(None),
        now: Arc::new(|| 0),
        storage_api: base.clone(),
        google_api: base,
    }
}

#[tokio::test]
async fn only_an_unauthorized_response_discards_the_cached_token() {
    let delivery = delivery_reading_from(String::new());
    for status in [200, 403, 429, 500, 401] {
        *delivery.token.lock().await = Some(CachedToken {
            value: "cached-token".into(),
            expires_at: 3600,
        });
        let response = reqwest::Response::from(
            axum::http::Response::builder()
                .status(status)
                .body(String::new())
                .unwrap(),
        );
        delivery.note_status(&response).await;
        assert_eq!(delivery.token.lock().await.is_none(), status == 401);
    }
}

/// Serve `respond` on loopback and record each request's path, query and
/// `Range` header, in that order.
async fn storage_stub(
    respond: fn() -> axum::response::Response,
) -> (String, Arc<std::sync::Mutex<Vec<String>>>) {
    let seen = Arc::new(std::sync::Mutex::new(Vec::new()));
    let log = Arc::clone(&seen);
    let router = axum::Router::new().fallback(
        move |uri: axum::http::Uri, headers: axum::http::HeaderMap| async move {
            let range = headers
                .get(reqwest::header::RANGE)
                .and_then(|value| value.to_str().ok())
                .unwrap_or("")
                .to_string();
            log.lock().unwrap().push(format!("{uri} {range}"));
            respond()
        },
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    (format!("http://{address}"), seen)
}

/// The size is what the resumable upload declares up front, so a wrong one
/// finishes a truncated file or never finishes at all.
#[tokio::test]
async fn the_staged_size_is_read_from_the_named_object() {
    use axum::response::IntoResponse;
    let (base, seen) = storage_stub(|| axum::Json(json!({ "size": "4096" })).into_response()).await;
    let delivery = delivery_reading_from(base);

    assert_eq!(
        delivery.object_size("codetrial/abc.mp4", "token").await,
        Ok(4096)
    );
    assert_eq!(*seen.lock().unwrap(), ["/b/staging/o/codetrial%2Fabc.mp4 "]);
}

/// A chunk is uploaded as returned, so its bytes have to be the object's.
#[tokio::test]
async fn a_range_returns_the_bytes_the_object_holds() {
    use axum::response::IntoResponse;
    let (base, seen) =
        storage_stub(|| (axum::http::StatusCode::PARTIAL_CONTENT, "hello").into_response()).await;
    let delivery = delivery_reading_from(base);

    assert_eq!(
        delivery
            .object_range("codetrial/abc.mp4", "token", 10, 14)
            .await,
        Ok(b"hello".to_vec())
    );
    assert_eq!(
        *seen.lock().unwrap(),
        ["/b/staging/o/codetrial%2Fabc.mp4?alt=media bytes=10-14"]
    );
}

/// The id found is the file a resumed transfer continues into, so a wrong one
/// writes into a file that is not this recording's, and a missed one uploads
/// a second copy.
#[tokio::test]
async fn the_duplicate_search_returns_the_file_it_found() {
    use axum::response::IntoResponse;
    let (base, seen) =
        storage_stub(|| axum::Json(json!({ "files": [{ "id": "drive-file-7" }] })).into_response())
            .await;
    let delivery = delivery_reading_from(base);
    assert_eq!(
        delivery.existing_file("rec-1", "token").await,
        Ok(Some("drive-file-7".to_string()))
    );
    assert!(seen.lock().unwrap()[0].starts_with("/drive/v3/files?q="));

    let (base, _) = storage_stub(|| axum::Json(json!({ "files": [] })).into_response()).await;
    let delivery = delivery_reading_from(base);
    assert_eq!(delivery.existing_file("rec-1", "token").await, Ok(None));
}

/// The chunks go to the URI the session answered with, so any other one sends
/// the recording nowhere.
#[tokio::test]
async fn an_upload_session_is_the_location_it_answered_with() {
    use axum::response::IntoResponse;
    let (base, seen) = storage_stub(|| {
        (
            [(
                axum::http::header::LOCATION,
                "https://upload.example/session-9",
            )],
            "",
        )
            .into_response()
    })
    .await;
    let delivery = delivery_reading_from(base);
    assert_eq!(
        delivery
            .upload_session("interview.mp4", "rec-1", "token")
            .await,
        Ok("https://upload.example/session-9".to_string())
    );
    assert_eq!(
        *seen.lock().unwrap(),
        ["/upload/drive/v3/files?uploadType=resumable&supportsAllDrives=true "]
    );
}

/// An object that reports no bytes has nothing to upload, and declaring a
/// zero-length session would finish an empty file.
#[tokio::test]
async fn an_empty_staged_object_is_refused() {
    use axum::response::IntoResponse;
    let (base, _) = storage_stub(|| axum::Json(json!({ "size": "0" })).into_response()).await;
    let delivery = delivery_reading_from(base);

    assert!(
        delivery
            .object_size("codetrial/abc.mp4", "token")
            .await
            .is_err()
    );
}

/// A 401 on either read means the remembered token is dead, so the next call
/// has to mint a new one instead of failing the same way until it expires.
#[tokio::test]
async fn a_rejected_token_is_forgotten_by_both_reads() {
    use axum::response::IntoResponse;
    let (base, _) = storage_stub(|| axum::http::StatusCode::UNAUTHORIZED.into_response()).await;
    let delivery = delivery_reading_from(base);
    let remember = |delivery: &GoogleDelivery| {
        *delivery.token.try_lock().unwrap() = Some(CachedToken {
            value: "stale".to_string(),
            expires_at: i64::MAX,
        });
    };

    remember(&delivery);
    assert!(
        delivery
            .object_size("codetrial/abc.mp4", "stale")
            .await
            .is_err()
    );
    assert!(delivery.token.try_lock().unwrap().is_none(), "object_size");

    remember(&delivery);
    assert!(
        delivery
            .object_range("codetrial/abc.mp4", "stale", 0, 4)
            .await
            .is_err()
    );
    assert!(delivery.token.try_lock().unwrap().is_none(), "object_range");
}
