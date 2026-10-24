# Reply latency observability

The Rust agent logs one `codetrial latency {JSON}` line at each point a reply
passes on the server. `python3 scripts/analyze-latency.py server.log` joins
them into a per-reply waterfall, a chronological timeline and a closing
`key=value` summary. `--summary` prints only the summary, `--json` prints
everything, and omitting the filename reads stdin. Log prefixes from journald
or containers are accepted. The run fails when no record is found; `--strict`
also fails on any rejected record or when no reply with a known input origin
reached native capture. Provider-only output remains visible but cannot establish
a healthy input-to-capture latency verdict. No
transcript, tool arguments, responses, credentials or candidate media enter
these records.

## One trace per reply

Each reply is a trace keyed by `(pid, room, session, turn)`. `socket` is where
the record was written and `origin_socket` where its input arrived, so a reply
that waits through a socket replacement stays one turn and its total includes
the stall. Recovery briefings preserve that origin; they do not restart the
wait. The origin is the input the reply answers:

| Origin | Meaning |
| --- | --- |
| `last_voiced_pcm` | The latest voiced 10 ms input frame the room loop consumed. An energy threshold, not an acoustic speech-end detector. |
| `prompt` | A platform prompt sent to the model. |
| `transcript` | A candidate transcript with no voiced frame before it. |
| `provider_voice_end` | Gemini's end of speech with no voiced frame before it. |
| `provider_event` | Output nothing local asked for. Reads near zero by construction and is kept out of percentiles. |

Until the reply's first output, the latest input wins: speech after an idle
prompt, or a prompt after a stray noise frame, moves the origin. Voiced frames
after Gemini's end of speech do not, until Gemini reports a new start. Input
that arrives once output has begun, whether barge-in speech or a prompt queued
behind the generation, waits for the next reply instead of moving or ending
this one, and survives either a normal completion or an interruption. Trailing
transcripts alone do not queue another reply, and the runtime
late-transcript grace also applies to tracing.

Generations a pause or a thinking hold has cut off locally are closed then, as
`paused` or `held`, discarding queued input from before that local cut. Their late provider events are not attributed to the reply
that follows. An `interrupted` before any output keeps the clock, because the
barge-in that caused it is the input still waiting for an answer.

## Stages

| Stage | Written when |
| --- | --- |
| `tool_response` | A tool batch's responses were sent, with its execution, board resend and send times and the declared names of the tools it answered. A batch that failed to send is recorded but does not move `last_tool_response_ms`. |
| `audio_accepted` | The first chunk that completes a frame was accepted for framing; a shorter chunk before it waits in the frame buffer and is not this stage. Logged after the handler; `enqueued` says whether a complete frame entered the worker queue. |
| `native_audio_capture` | The worker finished LiveKit `capture_frame` for that chunk's first frame; `captured` says whether it succeeded. |
| `native_audio_cancelled` | That frame was cancelled before capture, including a chunk still waiting for enough PCM to form a frame. |
| `audio_enqueue_failed` | The first frame could not enter a full or closed worker queue. It is not a native-capture attempt or a successful latency sample. |
| `turn_complete`, `interrupted` | The provider ended the generation. |
| `paused`, `held` | A local cut ended it. |
| `socket_replaced` | The socket was replaced. A reply without accepted audio keeps waiting (`continuing=true`), including tool-only generations. |

## Waterfall

With `CODETRIAL_GEMINI_TOOL_ACK` set, a tool round that holds back the reply a
candidate is waiting for, and has heard nothing of, queues a short
acknowledgement generated once per interview in the session's voice and accent
(`gemini-3.1-flash-tts-preview`, on the report keys). It plays at most once per
thing the candidate said, never while they are speaking again, and never
before a goodbye. It is not the reply: the watchdog and this record still wait
for the reply's own audio. Its first accepted chunk cancels the remaining clip
and clears buffered filler before the reply plays. Audio already transmitted
cannot be recalled. Cancelled queue entries are skipped without capture;
`output_queue_frames` can include them until the worker drains them. The record
stamps it as `tool_ack_ms`, and the summary's `tool_ack_lead` is the reply's native
capture less that stamp: how much sooner the candidate heard something. An
interview whose clip is not ready yet, or failed to generate, plays nothing.

Offsets share the origin and use the agent's monotonic clock. A milestone seen
before an origin that later moved is negative, and one never observed is
`null`. `observed_ms` may be negative when provider receipt precedes the first
local input. It uses a fixed session clock to order events even when a
later input moves the reply origin. The analyzer partitions origin to first native capture into
consecutive segments, so they sum to `server_total_ms`; `unattributed` absorbs
per-field millisecond rounding.

| Segment | Span |
| --- | --- |
| `candidate_yield` | Origin to the candidate choosing "Your turn is done", when that came inside the silence window. The candidate's own time; it replaces `silence_window`. |
| `silence_window` | A baseline allowance for a `last_voiced_pcm` origin: configured `GEMINI_SILENCE_MS`, capped by observed END or output. This is not measured provider endpointing time. |
| `endpointing` | The rest of the wait for Gemini's end of speech: uplink, provider voice detection, and whatever Gemini does before it reports END. |
| `response_start` | End of speech, or origin without one, to the first provider output. |
| `tool_decision` | The same span when that output was a tool call. |
| `tool_rounds` | First tool call to the last tool response sent, including any further model rounds between batches. |
| `post_tool_response` | Last tool response to first audio accepted. |
| `output_to_audio` | First output (an output transcript) to first audio accepted. |
| `output_queue` | Audio accepted to the worker dequeuing its first frame. |
| `native_capture` | LiveKit `capture_frame` of that frame. |

Overlays are independent spans, not additional waterfall segments.
`tool_handler_ms` is the local share of `tool_rounds` (execution, board resend
and send);
`provider_event_queue_ms` is from receipt of the provider message, before
parsing, to the start of the audio handler. It can begin before the reply origin when the
room loop consumes later microphone frames before handling that message.
`context_tokens` is the prompt size of the last single-generation turn before
the reply, and a cold socket clears it until its own first report. The summary
bands `beyond_window` by it (`context_0k_8k` and up) and fits
`context_slope_ms_per_1k`, a least-squares slope in milliseconds per thousand
tokens fitted within each kind of reply, so a long interview shows whether
replies slow as the conversation grows rather than that tool replies, which cost
more, come later. `stale_drop_ms`, `enqueue_ms` and `handler_tail_ms` split the audio handler
around the enqueue, the last being what the handler does after it, the
agent-state update included; `output_queue_frames` and `playout_ahead_ms` say how much
audio was already queued ahead of the reply's first frame, which separates a
first frame waiting behind earlier audio from one waiting on the worker. Adding overlays to the waterfall double counts time.

Gemini can report END in the same message as its first output, so END alone
does not separate waiting from answering; `end_with_output` counts the replies
whose first output came within 10 ms of END, where the segment named
endpointing also holds the model's own start. The configured silence window is a
comparison allowance, not proof of provider time spent waiting: explicit
yielding can end input sooner, and inference can overlap voice detection.
A yield ends the audio stream, so Gemini answers without waiting out the
window, and the trace stamps it as `candidate_yield_ms`. `beyond_window` is
total time outside the allowance or the yield, not measured inference. `transcript_before_end`
reports how long before END the first input transcript of the utterance
arrived, in whole milliseconds; zero means the words came with END and
cannot inform an earlier end-of-turn decision, and a negative value means they
came after it. The
dominant segment excludes both, and a reply is slow when its time
beyond the allowance reaches `--slow-ms` (1,500 by default). A reply at or past
`--stall-ms` (5,000 by default) is a stall. The threshold is a flag, not a
diagnosis: it sits in the gap between ordinary slow starts and sockets that
stopped answering, and a reply past it is reported as a rate, `stalls`, and
left out of every distribution, where a few would set the mean and the tail;
`beyond_window_with_stalls` keeps them in for the tail they set, `worst` still
lists them, and a sample of nothing but stalls is `SLOW`. Statistics separate origins, count only captured
replies, and report turns without capture by their last audio stage and by
whether a tool round came first (`unmeasured_by_tool_tool`,
`unmeasured_by_tool_no_tool`), since uncaptured replies are mostly the slower
kind. `kind_<origin>_tool` and `kind_<origin>_no_tool` split `beyond_window` by
whether a tool round came before the reply's first audio; evidence recorded
after it began does not make a tool reply. Percentiles
interpolate between ranks, to a tenth of a millisecond, and a statistic over
fewer than 20 replies is marked `small_n`: its p95 then lies between its two
largest values. Compare matched yield settings and input recordings; this
budget comparison cannot substitute for browser-perceived E2E measurement.

## Comparing two runs

`python3 scripts/analyze-latency.py --compare before.log after.log` compares
two logs kind of reply by kind; several sessions are pooled by concatenating
their logs. Two runs' overall medians mislead on their own: provider speed
moves a run's median by a few hundred milliseconds with nothing else changed,
and a reply preceded by a tool round waits for a second model pass, so a run
whose replies take fewer tool rounds is faster without any reply getting
faster. The comparison therefore reports, per origin, the share of replies a
tool round came first (`tool_share` in the single-log summary), the median of
each kind with a 95% bootstrap interval for its shift, and the change in the
mean split exactly into mix, from a different share of tool replies, and
speed, from each kind getting faster or slower. Stalls are compared as rates.
The intervals resample replies one by one and so ignore what replies in one
session share; interleave the two arms' sessions so that provider drift falls
on both alike.

## What is not measured

Gemini Live is one combined audio and model pipeline. These records cannot
separate its speech recognition, endpointing, inference and synthesis.
Native capture also does not prove the candidate heard anything: LiveKit
transport, browser jitter buffering, autoplay and the audio device on the way
out, and input transport before the agent on the way in, are outside this
record. A browser-perceived figure needs a matched audio measurement in the
browser.

## Browser audio smoke evidence

The credentialed `voice` and `barge` browser-check flows also capture
`jimAudio`: received byte/packet deltas, peak decoded RMS, playback-blocked
state and a bounded result per observed audio subscription. The harness uses
LiveKit's public track-specific `getRTCStatsReport()` and an analyser on the
same native track attached as Jim. It records no device labels or audio
samples.

Sampling waits up to 120 seconds for Speaking, then samples at 100 ms
intervals for at most 10 seconds, stopping early once both counters grow and
RMS exceeds 0.001. Counter resets and receiver changes start a new baseline;
different track generations never share deltas. At most 64 subscriptions are
sampled; exceeding that limit is an unavailable observation, not a healthy
verdict.

`unavailable` means there were insufficient counter pairs or decoded samples,
no attached Jim track, no Speaking interval, cancellation, a sampling error or
a subscription limit. `transport-silent` means bytes or packets, either one,
did not grow across the observed counter pairs, and `decoded-silent` means both
grew but decoded RMS stayed at or below the floor. These are separate failures.
The capture retains failed observations, and each resubscription that keeps its
track through its sampling window must pass independently. A generation whose
track is replaced, unsubscribed or disconnected before Speaking or within its
first second of it, before both counters grew and RMS exceeded the floor, is
marked `superseded`, since losing the track cut its window short and a fresh
analyser first reads near zero; it cannot fail an otherwise healthy
replacement, and the last generation must pass. A generation that sampled
longer is judged on its evidence even if its track then ends. Unmeasured
counters and RMS are `null`, not observed zeroes. This measures transport and
decoded signal during Speaking, not output-device audibility, physical level or
per-word delivery. The RMS floor is a smoke-check threshold, not a calibrated
speech detector. Local loopback tests do not replace the isolated provider run.

## Validation

The credential-free gate covers the trace lifecycle, the waterfall and the
parser. For a live comparison, run the Rust voice and barge-in flows in
`scripts/browser-check.sh` against the same provider, region, input recording
and silence setting. `BROWSER_CHECK_CAPTURE` includes a `latencyLog` field with
the latency lines the harness saw; pipe it into the analyzer. The harness keeps
a bounded tail of agent output, so this is a smoke test, not an archive of a
long session.

Input audio is sent to Gemini one complete 10 ms LiveKit frame at a time. A
two-frame batch held its first frame for another frame period, including the
last frame of an utterance that ended on an odd frame, and the endpointing
clock does not start until Gemini has it. This removes local batching wait, not
provider inference or network time. It doubles websocket messages from 50 to
100 per second while sending the same PCM bytes. Partial frames still buffer
and flush when the track ends.

`silence_window` is usually the largest segment, and the one most directly
traded against interrupting a candidate who paused to think. The default is 1.5
seconds, down from 3 seconds, so a reply to unyielded speech starts about 1.5
seconds sooner. `END_SENSITIVITY_HIGH` is sent beside it; it is the API's
documented default, named so the window is measured against a known value, and
it earns no latency of its own. A candidate who pauses longer mid-sentence is
answered sooner too, which the thinking hold, `GEMINI_SILENCE_MS` and
`GEMINI_END_SENSITIVITY=LOW` exist to absorb; compare
interruptions as well as latency before moving either default again.

## Design lineage

The breakdown follows the per-turn analysis in livekit-traces-analyzer, which
splits each reply into speech recognition, end-of-turn detection, the
tool-decision model call, tool execution and the model call after it, with the
remainder shown as overhead, and which surfaced a silent loss of spans when an
export file was named differently. CodeTrial keeps the tool split and the
residual, joins asynchronous records by identity rather than by neighboring
lines, and refuses an empty analysis. It uses milestones measured on its own
clock instead of estimating provider stages it cannot see.

## Synthetic LiveKit audio suite

`python3 scripts/measure-audio-latency.py --output /tmp/audio-local` builds the
`audio-latency` example against this checkout's locked dependencies and repeats
three trials of the 100 ms and 20 ms native source queues, alternating their
order. Each trial contains one labelled warmup pulse and twenty measured
pulses. `--queues`, `--iterations`, and `--pulses` select the experiment. Output
must be a new directory, created only after the build succeeds; an earlier
experiment is never overwritten.

The default `--mode local` connects a native source to a local track sink and
needs no service. `--mode room` publishes to a fresh, uniquely named LiveKit
room and measures the remote decoded PCM sink. Load `LIVEKIT_URL`,
`LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` into the environment first, using
the selected private config. No Gemini requests, candidate media, or interview
dispatch are involved. The room mode creates ordinary synthetic participants
and consumes LiveKit connection minutes. On Linux x86_64 the runner uses
`scripts/fetch-clang.sh` unless `CXX` already selects a compiler.

The probe sends 24 kHz mono PCM in ten-millisecond frames, delivered in 100 ms
bursts. A 100 ms, 1 kHz tone follows 300 ms of silence in each one-second trial
window, and a final silent second follows the last pulse. RMS hysteresis detects
an onset after decode. Both participants share one process and one monotonic
clock, so host clock synchronization is not an assumption.

An onset counts for a pulse only within the one-second delivery window after
its send, which ends no later than the next pulse's send. An onset in a gap
between the window's deadline and the next send is recorded as late. Without an
embedded pulse identity, a delivery slower than the whole period can land in
the next pulse's window and look fast there; the pulse it belongs to is then
missing, and under a constant overrun the warmup is always one of them.
Variable delays and losses can still alias individual samples; this suite does
not prove pulse identity or measure latency beyond its matching window. Zero,
multiple or late onsets make a sample unsuccessful, the warmup included. Raw
records include the send offset, first tone-frame capture completion, detected
delivery latency, and on-time and late onset counts. The probe writes them
before leaving the room, so a capture timeout or a failed unpublish or close
cannot discard what was measured; the file retains measurement errors and
teardown errors separately, and any of them exits nonzero. A pending teardown
marker is saved first, then updated after every unpublish and close has been
attempted. Each cleanup operation has a five-second deadline; a failed save
also cannot skip cleanup. The raw result uses schema version 3; earlier files
without teardown status are rejected. This is an acoustic onset estimate with
ten-millisecond frame resolution; codec ringing, loss concealment, scheduling
and jitter buffering can affect detection. It is not sample-accurate waveform
correlation.

`local` measures source buffering and sink delivery; `room` additionally
includes encoding, the network and SFU, and decode/jitter buffering. Neither
measures a browser's speaker output, microphone hardware, Gemini inference,
or production worker backlog. Capture completion is reported separately: it
can precede delivery. In the pinned native implementation the configured
source queue is a backpressure threshold; capacity is twice that threshold.
Changing it can move waiting into the application worker instead of making
the listener hear a reply sooner. Repeated trials are essential: an arbitrary
phase relative to the native ten-millisecond timer alone can change onset
latency by nearly a frame.

Each experiment saves `manifest.json` with revision, whether tracked files
differ from it, one digest over the manifest, lockfile, build script and every
file under `src/` and `examples/`, the executable hash, the Rust and C++
compiler versions, and host identity. Each trial saves raw JSON.
`summary.text`, `summary.json`, and `summary.csv` pool the measured samples by
mode and queue size, retaining attempted, delivered, missing and late counts
alongside mean, p50, p95, p99 and maximum for capture and delivery. Warmup is
excluded from the statistics, and a missing warmup is counted separately.
Percentiles interpolate ranks; fewer than twenty deliveries is labelled
`small_n`. These are descriptive sample statistics, not confidence intervals or
evidence that the settings caused a difference. Keep endpoint, host, workload,
binary and network conditions matched when comparing experiments, and inspect
individual trial files before pooling.

The report tool also serves as an acceptance check:

```sh
python3 scripts/audio-latency-report.py /tmp/audio-local/iter-*.json
python3 scripts/audio-latency-report.py /tmp/audio-local/iter-*.json \
    --format json --max-p95-ms 50 --max-missing-rate 0
```

Thresholds are experiment-specific and compare unrounded statistics; only the
printed values are rounded. The default refuses any unsuccessful sample. A
report with no delivered samples, or with a missing warmup, always fails,
including when a nonzero missing-rate budget is allowed. Invalid data, an empty
or interrupted run, a trial that left no result, failed or incomplete teardown,
invalid timing windows, or the same run given twice fails rather than producing
a healthy verdict; the runner then writes the refusal reason in place of
statistics, as a JSON error object and a one-column CSV in the machine-readable
summaries. The ordinary gate checks the report's validation, statistics, export
formats and budget exit statuses; real-room experiments are separate from the
credential-free gate.

### Lessons from lk-latency

The suite adapts the methodology of
[lk-latency](https://github.com/haffi96/lk-latency), a LiveKit video latency
harness: fresh rooms per iteration, raw timing archives, build identity and
pooled statistics. It keeps CodeTrial's released SDK rather than the
separately pinned revision that harness needs for per-stage hooks, so it does
not invent encoder, network or decode timestamps it cannot observe. Several
rules carry over. A run that never delivers media cannot pass. Tracks are
unpublished before the room closes, so shutdown negotiation does not look like
a media failure. Credentials reach the probe through the environment, never
argv. Every timing belongs to an explicit pulse, and an ambiguous match is
rejected rather than resolved in favor of the fastest. The endpoint is named
for what it is, decoded PCM at the sink, with no claim about audible output.
