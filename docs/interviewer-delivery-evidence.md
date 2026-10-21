# Cancelled interviewer output

Output transcription describes generated words, not a delivery receipt. A
provider generation can finish while native audio is still queued. Successful
native capture does not establish physical audibility, and transcription has
no word-to-PCM alignment from which to reconstruct a heard prefix.

## Storage

Keep raw transcript text, line positions, segment ids and turn counts intact.
Store delivery uncertainty separately, keyed by interviewer transcript line.
The set is monotonic for the session and independent of recognized text;
spoken words cannot create or remove a cancellation record. Candidate lines
are never marked or rewritten.

Attribute accepted audio's existing playout deadline to its interviewer line.
Audio received before the first transcript fragment waits for that line to
open. This waiting attribution belongs to that generation and socket;
completion (after its final padded audio frame), replacement, or a cut ends
the wait if no line opened. It cannot
attach to a later generation. Retain all lines with pending deadlines, including generations already
closed by TurnComplete. Every cut marks the lines with audio still queued. It
also marks the open interviewer line when it stops something: a running
generation, or completed output still playing. A cut that stops nothing, such
as a pause after the audio drained, marks nothing. This covers provider
interruption, candidate stale-playout cancellation, the stale-playout backstop
on the next generation's audio, thinking holds, pause and socket handoff.
Partly captured and never captured turns use the same policy; no heard prefix
is inferred.

Output transcription trails audio, so a line opened after a completion and
before the next generation's audio cannot be told apart from that output's
trailing words. A cut of completed playout therefore marks such a line, and
fragments arriving after the cut stay marked until the next generation's
first audio. That generation's words ahead of its audio are over-marked;
nothing after its first audio is. Cancelled trailing words are never kept as
delivered.

A cut seals the line it marks, and the end of a window seals the line the
window marked. A later fragment closes the sealed interviewer turn and opens
a new line, so a reply is never written into omitted text or ordered before
the candidate words that cut its predecessor. Audio that arrives while the
open line is sealed waits for the new line. The candidate's turn is not
closed. A sealed line can split one generation into two transcript rows; the
raw words are unchanged.

A cut during generation marks later transcript fragments until the first
TurnComplete received, whether dispatched or swallowed. A cut after a
completion is a playout cut, even if trailing fragments arrived, and ends at
the next audio rather than at a completion that may never come. So is a cut of
a stalled generation, whose completion may also never come. A completion that
brings no audio does not end a playout window either. This evidence window
is independent of the transport's interruption guard, which a late fragment
can clear. Ambiguous ordering may over-mark a subsequent generation until that
boundary. This window belongs to one provider socket; replacing that socket
closes it, cold or resumed, so the new socket's first reply starts normally.
A local cut after completion uses the playout window instead. After the boundary, subsequent turns start normally. Protected
greeting and closing paths that keep their audio are not marked.

## Readers

Recovery (cold, resumed and compressed), interim review, the phase judge's
prompt and the report substitute the whole marked line's text with
`[Interviewer output cancelled; delivery uncertain; generated words omitted.]`.
Transform before taking a slice or inserting the behavioral-round marker, so
line indexes remain correct. Omitting whole turns prevents bounded transcript
tails from separating a marker from the words it qualifies.

Conversation lookup retains raw text, search and byte offsets, with a
`delivery: "uncertain"` field on every affected row, and `nextDelivery:
"uncertain"` when the page ends inside an affected turn.
Include that field in serialized page budgets. Raw text remains useful for
finding a possibly heard idea, but does not establish delivery.

No reader may infer that a marked question, hint, clarification or follow-up
reached the candidate, or penalize a missing answer. Ignore older notes or
restored memory that assume delivery. Candidate speech and independent
engineering evidence remain usable. Hint counts and independence constraints
remain unchanged: raw interviewer text still participates in the server's
check against crediting a candidate for repeating an interviewer-supplied idea.
Round-boundary detection and candidate-evidence grounding also keep raw text.
Continuity does not treat a cancelled interviewer line as answering the
preceding candidate line.

A follow-up raised only in marked output is not established as raised for the
report; candidate reasoning remains assessable independently. When follow-ups
were released, the server conservatively stamps a negative raised judgment as
null if any interviewer output was cancelled, since the omitted words could
have contained that question. A positive judgment from other evidence remains
positive. This may withhold an unrelated negative judgment; it does not claim
that a generated question was never raised. In sessions with ordinary
barge-ins this can make every negative follow-up judgment unknown, an accepted
cost of preserving uncertainty without recording a new release-position state. The behavioral
question and no-repeat rules remain: clarify it if the candidate asks, without
automatically asking it again. When any marked line falls in the behavioral
round, recovery conservatively avoids a new question or follow-up. It adds
that uncertainty to what the recovered window shows of the round's opening,
including an opening the window lost, rather than replacing it. A marked
coding wrap-up still in flight at the transition may therefore prevent a
STAR question that was never generated from being asked after recovery. This
is an accepted loss of assessment opportunity: without aligned delivery or a
new semantic judgment, the platform cannot distinguish a wrap-up-only turn
from one that also began the question. It follows the existing rule for a
missing behavioral opening: unsupported STAR parts receive the existing
neutral `session_timing` / `skipped` bookkeeping at session end, without
blaming the candidate or awarding credit. The existing declined-probe
exception keeps those parts unassessed rather than skipped. A later
unmarked reply does not retrospectively establish which question or follow-up
the marked turn contained. This change sends no additional live notice on
a local cut and does not rewind phase evidence or request an assessment call.

Browser transcripts, saved history, replay and export retain recognized text
and existing behavior. This bundle changes model-facing evidence, not those
presentation surfaces. Unmarked output is not proof of physical audibility.
Audio cancellation, reply debt, timers, retry and spend limits, recording
retention, rubric 1 and report schema 3 remain unchanged.

## Contract and validation

Bundle 39 uses live prompt 30 and report prompt 23. The live component covers
recovery, conversation lookup and phase judging; the report component covers
interim and final assessment. Update server and browser constants,
prompt goldens and their recorded digest together. No schema migration is
needed, and existing reports retain their original contract.

Handler tests cover both ordinary cancellation paths, late fragments and the
next completion, fragments before and after a candidate playout cut, the reply
that follows one, the backstop's ambiguous line, cold and resumed replacement
replies, fully delivered and protected controls. Metadata tests cover partial
capture, overlapping queued lines, audio before transcription, idle and
playing pauses, holds and handoffs, sealing, and the other queue cuts. Verify
every reader, behavioral-round insertion, stable conversation paging,
interim reviews that are not rewound and unchanged candidate lines.

The marking, window and sealing rules live in `InterviewerDelivery` in
`src/agent.rs`, where mutation testing reaches them. The event handlers that
call it are excluded from mutation testing in `.cargo/mutants.toml`, so
their wiring rests on the handler tests alone.
These checks establish local event handling, not provider ordering or physical
delivery; the separate conversational media benchmark measures real media.
