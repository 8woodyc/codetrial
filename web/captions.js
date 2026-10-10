/// Captions over the editor.
///
/// Keep completed interviewer turns readable while the candidate thinks.
/// Interviewer captions reveal gradually, including final copies. Candidate
/// captions retain their bounded window and idle timeout.

import { captionWindow } from "./lib.js";

let nodes = null;

export function initCaptions(deps) {
  ({ nodes } = deps);
}

const CAPTION_TICK_MS = 300;
const CAPTION_CHARS_PER_TICK = 8;

// Bound live captions until Jim's final text is fully revealed.
const CAPTION_MAX_CHARS = 160;
// Candidate captions and the listening placeholder should clear when idle.
const CAPTION_IDLE_HIDE_MS = 12000;
let captionIdleTimer = null;
let interviewerCaption = {
  id: null,
  text: "",
  shown: 0,
  timer: null,
  final: false,
  pendingDismissal: false,
};

// Each publish opens a separate stream, so replaced turns can arrive late.
const retiredInterviewerIds = new Set();
const retiredCandidateIds = new Set();
let candidateCaption = { id: null, text: "", replaced: false };

export function updateCaptions(
  speaker,
  text,
  id = null,
  { final = false, finalStream = final } = {},
) {
  if (!nodes.captionsText) return;
  if (speaker === "interviewer") {
    if (retiredInterviewerIds.has(id)) return;
    if (candidateCaption.id != null) candidateCaption.replaced = true;
    if (interviewerCaption.id !== id) {
      retireInterviewerCaption();
      if (nodes.captionsBar) nodes.captionsBar.scrollTop = 0;
      interviewerCaption = {
        id,
        text: "",
        shown: 0,
        timer: null,
        final: false,
        pendingDismissal: false,
      };
    }
    // The longest, not the latest. Every interim publish carries the whole turn
    // so far on a stream of its own, so one that lands out of order is a
    // shorter copy of what is already on screen, and taking it would rewind the
    // reveal to a few characters and replay the line.
    if (text.length > interviewerCaption.text.length)
      interviewerCaption.text = text;
    if (final) interviewerCaption.final = true;

    // Final text can arrive while audio is still queued.
    if (interviewerCaption.final) retireCandidateCaption();
    if (!interviewerCaption.timer) paceInterviewerCaption();
    return;
  }

  if (retiredCandidateIds.has(id)) return;
  const newCandidateSegment = candidateCaption.id !== id;
  if (newCandidateSegment) {
    retireCandidateCaption();
    candidateCaption = { id, text: "", replaced: false };
  }

  // A closing copy is not a new response to the interviewer.
  if (
    candidateCaption.replaced &&
    (finalStream || text.length <= candidateCaption.text.length)
  ) {
    return;
  }

  if (text.length > candidateCaption.text.length) candidateCaption.text = text;
  candidateCaption.replaced = false;

  // A new candidate segment supersedes Jim. Otherwise, only a final Jim
  // turn is retired; a live turn can resume after late candidate speech.
  if (newCandidateSegment || interviewerCaption.final) {
    retireInterviewerCaption();
  } else {
    clearTimeout(interviewerCaption.timer);
    interviewerCaption.timer = null;
  }
  const clipped = captionWindow(text, CAPTION_MAX_CHARS);
  nodes.captionsText.textContent = `[${speaker === "you" ? "You" : "Jim"}]: ${clipped}`;
  showCaptions();
}

export function paceInterviewerCaption() {
  const caption = interviewerCaption;
  caption.shown = Math.min(
    caption.text.length,
    caption.shown + CAPTION_CHARS_PER_TICK,
  );
  const retained = caption.final && caption.shown === caption.text.length;
  const text = retained
    ? caption.text
    : captionWindow(caption.text.slice(0, caption.shown), CAPTION_MAX_CHARS);
  nodes.captionsText.textContent = `[Jim]: ${text}`;
  if (retained && caption.pendingDismissal) {
    dismissCaptions();
    return;
  }
  showCaptions({ persistent: retained });
  if (caption.shown < caption.text.length) {
    caption.timer = setTimeout(paceInterviewerCaption, CAPTION_TICK_MS);
  } else {
    caption.timer = null;
  }
}

/// Completed interviewer turns remain available while the candidate thinks.
/// Other captions expire so an idle subtitle does not cover the editor.
export function showCaptions({ persistent = false } = {}) {
  if (!nodes.captionsBar) return;
  nodes.captionsBar.hidden = false;
  clearTimeout(captionIdleTimer);
  captionIdleTimer = null;

  if (persistent) return;

  captionIdleTimer = setTimeout(() => {
    nodes.captionsBar.hidden = true;
  }, CAPTION_IDLE_HIDE_MS);
}

/// Remember a code response until Jim's final text finishes revealing.
export function dismissCaptions() {
  if (
    !interviewerCaption.text ||
    retiredInterviewerIds.has(interviewerCaption.id)
  ) {
    return;
  }

  interviewerCaption.pendingDismissal = true;
  if (
    !interviewerCaption.final ||
    interviewerCaption.shown < interviewerCaption.text.length
  ) {
    return;
  }

  retireInterviewerCaption();
  clearTimeout(captionIdleTimer);
  captionIdleTimer = null;
  if (nodes.captionsBar) nodes.captionsBar.hidden = true;
}

function retireInterviewerCaption() {
  clearTimeout(interviewerCaption.timer);
  interviewerCaption.timer = null;
  interviewerCaption.final = false;
  interviewerCaption.pendingDismissal = false;
  if (interviewerCaption.id != null) {
    retiredInterviewerIds.add(interviewerCaption.id);
  }
}

function retireCandidateCaption() {
  if (candidateCaption.id != null) {
    retiredCandidateIds.add(candidateCaption.id);
  }
}
