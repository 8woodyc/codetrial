//! Synthetic PCM onset timing, using the application's pinned SDK and transport.
//! It times a native source through a local track or a LiveKit room to a
//! decoded sink; the interview's own candidate input path and Gemini are not in
//! it.
use futures_util::StreamExt;
use livekit::{
    options::TrackPublishOptions,
    prelude::*,
    webrtc::{
        audio_frame::AudioFrame,
        audio_source::{AudioSourceOptions, native::NativeAudioSource},
        audio_stream::native::NativeAudioStream,
    },
};
use serde_json::json;
use std::{
    borrow::Cow,
    error::Error,
    time::{Duration, Instant},
};
use tokio::sync::mpsc;

type Result<T> = std::result::Result<T, Box<dyn Error + Send + Sync>>;

/// Cap onset matching at one pulse period. Without an embedded pulse identity,
/// a longer delivery can alias into a later window; the warmup and final
/// trailing period expose constant overruns.
const DELIVERY_WINDOW_MS: u32 = 1000;

const SAMPLE_RATE: u32 = 24_000;
/// Ten milliseconds, the frame LiveKit's native source and stream work in.
const FRAME_SAMPLES: u32 = SAMPLE_RATE / 100;
/// A decoded frame this loud starts an onset, and the onset ends only once a
/// frame falls below the release level: the gap between them keeps a codec's
/// ringing from counting as another pulse.
const ONSET_RMS: f64 = 2000.0;
const RELEASE_RMS: f64 = 500.0;

#[tokio::main]
async fn main() -> Result<()> {
    rustls::crypto::ring::default_provider()
        .install_default()
        .ok();
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() != 4 || !matches!(args[0].as_str(), "local" | "room") {
        return Err("usage: cargo run --example audio-latency -- local|room QUEUE_MS PULSES OUTPUT.json (room uses LIVEKIT_URL/API_KEY/API_SECRET)".into());
    }
    let queue: u32 = args[1].parse()?;
    let pulses: usize = args[2].parse()?;
    if !(10..=500).contains(&queue) || !queue.is_multiple_of(10) || !(1..=1000).contains(&pulses) {
        return Err("QUEUE_MS must be 10..500 in steps of 10; PULSES must be 1..1000".into());
    }
    let mut rooms = Vec::new();
    let report = probe(&args[0], queue, pulses, &mut rooms).await;
    finish_probe(std::path::Path::new(&args[3]), report, &rooms).await
}

async fn probe(
    mode: &str,
    queue: u32,
    pulses: usize,
    rooms: &mut Vec<Room>,
) -> Result<serde_json::Value> {
    let source = NativeAudioSource::new(AudioSourceOptions::default(), SAMPLE_RATE, 1, queue);
    let track = LocalAudioTrack::create_audio_track(
        "latency-synthetic",
        RtcAudioSource::Native(source.clone()),
    );
    let mut stream = if mode == "room" {
        let url = std::env::var("LIVEKIT_URL")?;
        let key = std::env::var("LIVEKIT_API_KEY")?;
        let secret = std::env::var("LIVEKIT_API_SECRET")?;
        let name = format!(
            "latency-{}-{}",
            std::process::id(),
            codetrial::current_epoch_seconds()
        );
        let now = codetrial::current_epoch_seconds();
        let viewer_token =
            codetrial::token::livekit_observer_token(&key, &secret, "latency-viewer", &name, now)?;
        let publisher_token =
            codetrial::token::livekit_token(codetrial::token::LivekitTokenInput {
                api_key: &key,
                api_secret: &secret,
                name: "latency-publisher",
                identity: "latency-publisher",
                room: &name,
                metadata: "",
                now_seconds: now,
                agent: false,
            })?;
        let (viewer, mut events) = connect(&url, &viewer_token).await?;
        rooms.push(viewer);
        let (publisher, _) = connect(&url, &publisher_token).await?;
        // Held by `rooms` before publishing, so a failed publish still has
        // the room closed in teardown.
        rooms.push(publisher);
        rooms
            .last()
            .expect("publisher connected")
            .local_participant()
            .publish_track(
                LocalTrack::Audio(track.clone()),
                TrackPublishOptions {
                    source: TrackSource::Microphone,
                    ..Default::default()
                },
            )
            .await?;
        let remote = tokio::time::timeout(Duration::from_secs(20), async {
            while let Some(event) = events.recv().await {
                if let RoomEvent::TrackSubscribed {
                    track: RemoteTrack::Audio(audio),
                    ..
                } = event
                {
                    return Ok(audio);
                }
            }
            Err("viewer event stream ended")
        })
        .await??;
        NativeAudioStream::new(remote.rtc_track(), SAMPLE_RATE as i32, 1)
    } else {
        NativeAudioStream::new(track.rtc_track(), SAMPLE_RATE as i32, 1)
    };
    let clock = Instant::now();
    let (onsets, mut received) = mpsc::unbounded_channel();
    let reader = tokio::spawn(async move {
        let mut active = false;
        while let Some(frame) = stream.next().await {
            let rms = (frame
                .data
                .iter()
                .map(|&s| f64::from(s).powi(2))
                .sum::<f64>()
                / frame.data.len().max(1) as f64)
                .sqrt();
            if rms > ONSET_RMS {
                if !active {
                    let _ = onsets.send(clock.elapsed().as_secs_f64() * 1000.0);
                }
                active = true;
            } else if rms < RELEASE_RMS {
                active = false;
            }
        }
    });
    // Continuous silence primes the codec and jitter buffer. The first pulse is
    // separately labelled warmup, so it cannot silently improve the distribution.
    let mut tick = tokio::time::interval(Duration::from_millis(100));
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    // FRAME_SAMPLES is a whole number of 1 kHz cycles, so every tone frame is
    // the same frame and joins the next without a click.
    let tone: Vec<i16> = (0..FRAME_SAMPLES)
        .map(|s| {
            (12000.0
                * (std::f64::consts::TAU * 1000.0 * f64::from(s) / f64::from(SAMPLE_RATE)).sin())
                as i16
        })
        .collect();
    let mut sends = Vec::new();
    let outcome: Result<()> = async {
        for _ in 0..=pulses {
            sends.push(send_window(&source, &mut tick, clock, Some(&tone)).await?);
        }
        // A trailing silent period gives the last pulse the same delivery
        // window as every other, instead of aborting the reader under it.
        send_window(&source, &mut tick, clock, None).await?;
        Ok(())
    }
    .await;
    reader.abort();
    let _ = reader.await;
    let arrivals: Vec<f64> = std::iter::from_fn(|| received.try_recv().ok()).collect();
    let sent: Vec<f64> = sends.iter().map(|&(at, _)| at).collect();
    let samples: Vec<_> = assign(&sent, &arrivals)
        .into_iter()
        .zip(&sends)
        .enumerate()
        .map(|(pulse, (onset, &(sent_ms, capture_ms)))| {
            json!({"sequence": pulse, "warmup": pulse == 0, "sent_ms": sent_ms, "capture_ms": capture_ms, "delivery_ms": onset.delivery_ms, "onsets": onset.onsets, "late_onsets": onset.late_onsets})
        })
        .collect();
    Ok(
        json!({"version": 3, "mode": mode, "queue_ms": queue, "sample_rate": SAMPLE_RATE, "frame_ms": 10, "burst_ms": 100, "delivery_window_ms": DELIVERY_WINDOW_MS, "requested_pulses": pulses, "error": outcome.as_ref().err().map(ToString::to_string), "teardown_complete": false, "teardown_errors": [], "samples": samples}),
    )
}

trait ProbeRoom {
    type Track;
    fn tracks(&self) -> Vec<Self::Track>;
    async fn unpublish(&self, track: &Self::Track) -> Result<()>;
    async fn close(&self) -> Result<()>;
}

impl ProbeRoom for Room {
    type Track = TrackSid;

    fn tracks(&self) -> Vec<TrackSid> {
        self.local_participant()
            .track_publications()
            .into_keys()
            .collect()
    }

    async fn unpublish(&self, track: &TrackSid) -> Result<()> {
        self.local_participant().unpublish_track(track).await?;
        Ok(())
    }

    async fn close(&self) -> Result<()> {
        Room::close(self).await?;
        Ok(())
    }
}

/// Teardown continues after errors, including a failed save. A pending marker
/// makes an interrupted teardown visible to a later standalone report too.
async fn finish_probe<R: ProbeRoom>(
    path: &std::path::Path,
    report: Result<serde_json::Value>,
    rooms: &[R],
) -> Result<()> {
    let mut failures = Vec::new();
    let mut report = match report {
        Ok(report) => Some(report),
        Err(error) => {
            failures.push(format!("probe failed: {error}"));
            None
        }
    };
    if let Some(report) = &report
        && let Err(error) = save_report(path, report)
    {
        failures.push(format!("save measurements: {error}"));
    }
    let mut teardown_errors = Vec::new();
    for (index, room) in rooms.iter().enumerate() {
        for track in room.tracks() {
            teardown_errors
                .extend(attempt(format!("unpublish room {index}"), room.unpublish(&track)).await);
        }
    }
    if !rooms.is_empty() {
        tokio::time::sleep(Duration::from_millis(400)).await;
    }
    for (index, room) in rooms.iter().enumerate() {
        teardown_errors.extend(attempt(format!("close room {index}"), room.close()).await);
    }
    failures.extend(teardown_errors.iter().cloned());
    if let Some(report) = &mut report {
        report["teardown_complete"] = true.into();
        report["teardown_errors"] = json!(teardown_errors);
        if let Err(error) = save_report(path, report) {
            failures.push(format!("save teardown outcome: {error}"));
        }
        if let Some(error) = report["error"].as_str() {
            failures.push(format!("measurement failed: {error}"));
        }
        if report["samples"]
            .as_array()
            .expect("probe samples")
            .iter()
            .any(|s| s["delivery_ms"].is_null())
        {
            failures.push("missing, late or ambiguous audio onsets".into());
        }
    }
    if failures.is_empty() {
        Ok(())
    } else {
        Err(failures.join("; ").into())
    }
}

/// One cleanup step, bounded so a stalled call cannot keep the rest from
/// running; the error text, if any, goes into the saved record.
async fn attempt(label: String, operation: impl Future<Output = Result<()>>) -> Option<String> {
    match tokio::time::timeout(Duration::from_secs(5), operation).await {
        Ok(Ok(())) => None,
        Ok(Err(error)) => Some(format!("{label}: {error}")),
        Err(_) => Some(format!("{label}: timed out")),
    }
}

async fn connect(url: &str, token: &str) -> Result<(Room, mpsc::UnboundedReceiver<RoomEvent>)> {
    Ok(tokio::time::timeout(
        Duration::from_secs(30),
        Room::connect(url, token, RoomOptions::default()),
    )
    .await??)
}

fn save_report(path: &std::path::Path, report: &serde_json::Value) -> Result<()> {
    std::fs::write(path, serde_json::to_vec_pretty(report)?)?;
    Ok(())
}

#[derive(Debug, PartialEq)]
struct Onset {
    delivery_ms: Option<f64>,
    onsets: usize,
    late_onsets: usize,
}

/// Match detected onsets to the pulses sent at `sent` milliseconds.
fn assign(sent: &[f64], arrivals: &[f64]) -> Vec<Onset> {
    sent.iter()
        .enumerate()
        .map(|(pulse, &sent_ms)| {
            let next = sent.get(pulse + 1).copied().unwrap_or(f64::INFINITY);
            let deadline = (sent_ms + f64::from(DELIVERY_WINDOW_MS)).min(next);
            let on_time: Vec<f64> = arrivals
                .iter()
                .filter(|&&at| at >= sent_ms && at < deadline)
                .map(|at| at - sent_ms)
                .collect();
            // Only the gap before the next send can be identified as late. A
            // whole-period delay can alias; see DELIVERY_WINDOW_MS. An onset
            // before the first send can only be noise.
            let late_onsets = arrivals
                .iter()
                .filter(|&&at| (at >= deadline && at < next) || (pulse == 0 && at < sent_ms))
                .count();
            // Multiple onsets are ambiguous, never a choice of the fastest one.
            let delivery_ms = match (on_time.as_slice(), late_onsets) {
                ([only], 0) => Some(*only),
                _ => None,
            };
            Onset {
                delivery_ms,
                onsets: on_time.len(),
                late_onsets,
            }
        })
        .collect()
}

/// One second: 300 ms of silence, then `tone` for 100 ms if given, then
/// silence. Returns the tone's send offset and how long its first frame took to
/// capture.
async fn send_window(
    source: &NativeAudioSource,
    tick: &mut tokio::time::Interval,
    clock: Instant,
    tone: Option<&[i16]>,
) -> Result<(f64, f64)> {
    let silent_frame = [0i16; FRAME_SAMPLES as usize];
    let mut sent_ms = 0.0;
    let mut capture_ms = 0.0;
    for frame in 0..100 {
        if frame % 10 == 0 {
            tick.tick().await;
        }
        let data = match tone {
            Some(tone) if (30..40).contains(&frame) => tone,
            _ => &silent_frame,
        };
        let at = Instant::now();
        if frame == 30 {
            sent_ms = clock.elapsed().as_secs_f64() * 1000.0;
        }
        tokio::time::timeout(
            Duration::from_secs(2),
            source.capture_frame(&AudioFrame {
                data: Cow::Borrowed(data),
                sample_rate: SAMPLE_RATE,
                num_channels: 1,
                samples_per_channel: FRAME_SAMPLES,
            }),
        )
        .await??;
        if frame == 30 {
            capture_ms = at.elapsed().as_secs_f64() * 1000.0;
        }
    }
    Ok((sent_ms, capture_ms))
}

#[cfg(test)]
#[path = "../tests/unit/audio_latency.rs"]
mod tests;
