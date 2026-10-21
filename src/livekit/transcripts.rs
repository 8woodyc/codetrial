//! Transcript segments on their way to the page, and the one task that sends
//! them.
//!
//! Each fragment either speaker says republishes its whole turn so far as a
//! LiveKit text stream: a deflate of the turn, then a header, a chunk and a
//! trailer, each waiting on the data channel, and before them a check that the
//! publisher is connected, which a signal reconnect can stretch to fifteen
//! seconds. Awaited in the room loop, that held the next Gemini event and the
//! candidate's microphone frames behind it, several times a second while
//! either side spoke. The loop now only queues a segment, the same way the
//! agent state attribute already had its own writer.
//!
//! Nothing is lost by sending less often than the loop queues. A turn's text is
//! cumulative under one segment id, so a newer view of a segment still waiting
//! replaces it where it stands, and the page patches one row per segment. The
//! order segments first appear in is kept, so a final follows its turn's
//! updates and the two speakers' turns go out in the order they were closed.

use std::collections::VecDeque;
use std::sync::{Arc, Mutex, PoisonError};
use std::time::Duration;

use ::livekit::prelude::{LocalParticipant, Room};
use tokio::sync::Notify;

/// One segment as the page shows it: the whole turn so far, under its id.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct Segment {
    pub(super) id: String,
    pub(super) text: String,
    pub(super) final_segment: bool,
    /// The candidate's identity for their own speech; `None` publishes as the
    /// agent participant.
    pub(super) identity: Option<String>,
}

/// The segments waiting to be sent, newest view of each.
#[derive(Debug, Default)]
pub(super) struct Pending {
    queue: VecDeque<Segment>,
}

impl Pending {
    /// Queues `segment`, replacing an update of the same segment still waiting
    /// rather than sending both. A final is never replaced: it is the last word
    /// on its segment, and nothing is queued under that id after it.
    pub(super) fn push(&mut self, segment: Segment) {
        if let Some(waiting) = self
            .queue
            .iter_mut()
            .find(|waiting| waiting.id == segment.id && !waiting.final_segment)
        {
            *waiting = segment;
        } else {
            self.queue.push_back(segment);
        }
    }

    pub(super) fn pop(&mut self) -> Option<Segment> {
        self.queue.pop_front()
    }

    pub(super) fn is_empty(&self) -> bool {
        self.queue.is_empty()
    }
}

#[derive(Default)]
struct Queue {
    pending: Pending,
    /// A segment has been taken off the queue and is still being sent.
    sending: bool,
    /// The interview's writer is gone; the task sends what is left and ends.
    closed: bool,
}

#[derive(Default)]
struct Shared {
    queue: Mutex<Queue>,
    /// Something was queued, or the writer closed.
    work: Notify,
    /// The queue ran empty with nothing in flight.
    drained: Notify,
}

impl Shared {
    fn lock(&self) -> std::sync::MutexGuard<'_, Queue> {
        // Nothing a panic could leave half-done is held under this lock.
        self.queue.lock().unwrap_or_else(PoisonError::into_inner)
    }

    fn idle(&self) -> bool {
        let queue = self.lock();
        queue.pending.is_empty() && !queue.sending
    }
}

/// The interview's transcript writer. Started on the first segment, and its
/// task ends once this is dropped and the queue has been sent.
#[derive(Default)]
pub(crate) struct TranscriptWriter {
    shared: Option<Arc<Shared>>,
}

impl TranscriptWriter {
    /// Queues a segment for the page without waiting on the room. A failed
    /// send is logged by the task: the transcript is already recorded, and the
    /// report is written from that record, so a segment the page misses is
    /// cosmetic.
    pub(super) fn publish(&mut self, room: &Room, segment: Segment) {
        let shared = self.shared.get_or_insert_with(|| {
            let shared = Arc::new(Shared::default());
            tokio::spawn(send_segments(room.local_participant(), Arc::clone(&shared)));
            shared
        });
        shared.lock().pending.push(segment);
        shared.work.notify_one();
    }

    /// Waits, at most `limit`, for everything queued to have been sent. The
    /// ending calls this before the report goes out, so the page has the last
    /// turns before it is handed the report, as it did when each publish was
    /// awaited in place.
    pub(super) async fn flush(&self, limit: Duration) {
        let Some(shared) = &self.shared else { return };
        let drained = async {
            loop {
                let notified = shared.drained.notified();
                tokio::pin!(notified);
                notified.as_mut().enable();
                if shared.idle() {
                    return;
                }
                notified.await;
            }
        };
        if tokio::time::timeout(limit, drained).await.is_err() {
            eprintln!(
                "transcript segments still unsent after {:.1}s; going on without them",
                limit.as_secs_f64()
            );
        }
    }
}

impl Drop for TranscriptWriter {
    fn drop(&mut self) {
        if let Some(shared) = &self.shared {
            shared.lock().closed = true;
            shared.work.notify_one();
        }
    }
}

async fn send_segments(participant: LocalParticipant, shared: Arc<Shared>) {
    loop {
        let next = {
            let mut queue = shared.lock();
            let next = queue.pending.pop();
            queue.sending = next.is_some();
            if next.is_none() && queue.closed {
                return;
            }
            next
        };
        let Some(segment) = next else {
            shared.drained.notify_waiters();

            // `notify_one` keeps a permit when nobody is waiting, so a segment
            // queued between the check above and this wait still wakes it.
            shared.work.notified().await;
            continue;
        };
        let options = super::session::transcript_stream_options(
            segment.id,
            segment.final_segment,
            segment.identity.as_deref(),
        );
        if let Err(error) = participant.send_text(&segment.text, options).await {
            eprintln!("publishing a transcript segment failed ({error}); continuing the interview");
        }
    }
}

#[cfg(test)]
#[path = "../../tests/unit/livekit/transcripts.rs"]
mod tests;
