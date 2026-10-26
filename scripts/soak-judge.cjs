// What the soak makes of the socket replacements the agent logs.
//
// Kept apart from scripts/browser-check.cjs so the verdict can be tested
// without a browser: it is a function of the `replacement:` lines and two
// flags, and every rule in it was learned from a run that went wrong.

const TRIGGERS = ["watchdog", "close", "goaway"];
const HANDOVERS = ["resumed", "cold_handoff", "degraded"];

// A provider that stalls on an utterance costs the reply watchdog's ten
// seconds and a replacement, and the replacement may have to be rebuilt locally
// when the socket it replaced was itself young. One such recovery is a
// property of the provider, not of the rotation under test; a second one in the
// same soak means the interview is not holding together.
const MAX_WATCHDOG_REBUILDS = 1;

// Why a rebuild after a reply timeout can be the provider's doing and not a
// defect: the replaced socket had offered no handle yet, or was itself a
// resumed one replaced young. A handle that was offered and refused is a
// resumption defect whatever made the replacement necessary.
const STALL_FALLBACKS = new Set(["no_handle", "resume_suppressed"]);

/// The complete lines of `log`: a trailing fragment has no newline yet, and
/// reading it now would print it once cut short and again when finished.
function completeLines(log) {
  const lines = log.split("\n");
  if (!log.endsWith("\n")) lines.pop();
  return lines;
}

/// The `replacement:` lines of one room, in the order logged, each once. A
/// replacement is told apart by the clock it was logged at, not by the whole
/// line, so the same one read from the agent's output and from the server's log
/// is not counted twice however the two spell the rest of it. A line that does
/// not carry a known trigger and handover is left out, so a renamed value turns
/// into a missing handover, which fails, and not into a silent pass.
function parseReplacements(log, room) {
  const seen = new Set();
  const replacements = [];
  for (const line of completeLines(log)) {
    if (!line.startsWith("replacement: ")) continue;
    // `room=` is the last field and a room name may hold spaces, so it is the
    // rest of the line and the other fields are read from what comes before.
    const at = line.lastIndexOf(" room=");
    if (at < 0 || line.slice(at + " room=".length) !== room) continue;
    const fields = Object.fromEntries(
      [...line.slice(0, at).matchAll(/(?:^|\s)([a-z_]+)=(\S+)/g)].map((m) => [
        m[1],
        m[2],
      ]),
    );
    if (!TRIGGERS.includes(fields.trigger)) continue;
    if (!HANDOVERS.includes(fields.handover)) continue;
    if (!fields.at || seen.has(fields.at)) continue;
    seen.add(fields.at);
    replacements.push({
      line,
      at: fields.at,
      trigger: fields.trigger,
      handover: fields.handover,
      fallback: fields.fallback ?? "none",
    });
  }
  return replacements;
}

/// Whether `replacement` is the proactive handover a GoAway asked for: the
/// conversation continued, from a handle or by the designed cold fallback.
function isRotation(replacement) {
  return (
    replacement.trigger === "goaway" && replacement.handover !== "degraded"
  );
}

/// pass, fail or inconclusive, with the reasons behind a failure; `pending`
/// before `final`, when nothing has failed yet.
///
/// A run in which Gemini sent no GoAway tested nothing about rotation, so it is
/// neither a pass nor a failure of it. A GoAway that was not followed by a
/// handover is a failure, and so is any rebuild from local state that the
/// provider's stalls do not account for.
function judgeSoak({ replacements, goAway, expired, final = true }) {
  const reasons = [];
  let watchdogRebuilds = 0;
  for (const r of replacements) {
    if (r.handover !== "degraded") continue;
    if (r.trigger === "watchdog" && STALL_FALLBACKS.has(r.fallback)) {
      watchdogRebuilds += 1;
    } else {
      reasons.push(
        `rebuilt from local state on trigger=${r.trigger} fallback=${r.fallback} at=${r.at}`,
      );
    }
  }
  if (watchdogRebuilds > MAX_WATCHDOG_REBUILDS) {
    reasons.push(
      `${watchdogRebuilds} rebuilds from local state after reply timeouts, more than the ${MAX_WATCHDOG_REBUILDS} a stalled provider can account for`,
    );
  }
  if (expired) {
    reasons.push("the server closed the socket before a held GoAway was spent");
  }
  if (reasons.length > 0) {
    return { verdict: "fail", reasons, watchdogRebuilds };
  }
  // Mid-run, no failure is not yet a result: the GoAway's replacement may be
  // up to its notice away, and a run that has seen none may still see one.
  if (!final) return { verdict: "pending", reasons: [], watchdogRebuilds };
  if (!goAway) {
    return {
      verdict: "inconclusive",
      reasons: ["no transport restart was requested during the soak"],
      watchdogRebuilds,
    };
  }
  if (!replacements.some(isRotation)) {
    return {
      verdict: "fail",
      reasons: ["a GoAway was seen but no handover followed it"],
      watchdogRebuilds,
    };
  }
  return { verdict: "pass", reasons: [], watchdogRebuilds };
}

/// Whether `log` has a complete line of this room that holds `needle`. The
/// advisory and its expiry are logged with the room, and a log that carries
/// other rooms' must not read as this one's.
function roomLogged(log, needle, room) {
  return completeLines(log).some(
    (line) => line.includes(needle) && line.includes(`room=${room}`),
  );
}

module.exports = {
  MAX_WATCHDOG_REBUILDS,
  completeLines,
  isRotation,
  judgeSoak,
  parseReplacements,
  roomLogged,
};
