import { test } from "node:test";
import assert from "node:assert/strict";

import judge from "../../scripts/soak-judge.cjs";

const ROOM = "interview-browser-1";

function line(fields, room = ROOM) {
  const { trigger, handover, fallback = "none", at = "1:00.000" } = fields;
  return `replacement: at=${at} resumed=${handover === "resumed"} trigger=${trigger} handover=${handover} fallback=${fallback} rotation_gap_ms=500 checkpoint_age=none owed=true room=${room}\n`;
}

const parse = (log, room = ROOM) => judge.parseReplacements(log, room);
const verdict = (log, flags = {}) =>
  judge.judgeSoak({
    replacements: parse(log),
    goAway: true,
    expired: false,
    ...flags,
  }).verdict;

test("a replacement line is read by its key=value fields, once", () => {
  const log = line({ trigger: "goaway", handover: "resumed" });
  const found = parse(log + log);
  assert.equal(found.length, 1);
  assert.equal(found[0].trigger, "goaway");
  assert.equal(found[0].handover, "resumed");
  assert.equal(found[0].fallback, "none");
});

test("the same replacement read from two logs is one replacement", () => {
  const fromAgent = line({ trigger: "watchdog", handover: "degraded" });
  const fromServer = fromAgent.replace("owed=true", "owed=true extra=1");
  assert.equal(parse(fromAgent + fromServer).length, 1);
});

test("another room's lines and unknown spellings are left out", () => {
  const other = line({ trigger: "goaway", handover: "resumed" }, "other-room");
  const renamed = line({ trigger: "goaway", handover: "continued" });
  assert.deepEqual(parse(other + renamed), []);
});

test("the first audio line and a cut-short trailing line are not replacements", () => {
  const firstAudio = `replacement first audio: at=1 resumed=true since_start_ms=9 room=${ROOM}\n`;
  const whole = line({ trigger: "goaway", handover: "resumed" });
  assert.deepEqual(parse(firstAudio), []);
  // Without its newline the line may be cut; it is read once it is finished.
  assert.deepEqual(parse(whole.trimEnd()), []);
  assert.equal(parse(whole).length, 1);
});

test("a GoAway followed by a resumed or designed cold handover passes", () => {
  assert.equal(
    verdict(line({ trigger: "goaway", handover: "resumed" })),
    "pass",
  );
  assert.equal(
    verdict(
      line({
        trigger: "goaway",
        handover: "cold_handoff",
        fallback: "checkpoint_before_handoff",
      }),
    ),
    "pass",
  );
});

test("a GoAway rebuilt from local state without the designed reason fails", () => {
  const result = judge.judgeSoak({
    replacements: parse(
      line({ trigger: "goaway", handover: "degraded", fallback: "no_handle" }),
    ),
    goAway: true,
    expired: false,
  });
  assert.equal(result.verdict, "fail");
  assert.match(result.reasons[0], /trigger=goaway fallback=no_handle/);
});

test("one recovered watchdog rebuild is tolerated, a second is not", () => {
  const rotation = line({
    trigger: "goaway",
    handover: "resumed",
    at: "3:00.000",
  });
  const stall = (at) =>
    line({
      trigger: "watchdog",
      handover: "degraded",
      fallback: "resume_suppressed",
      at,
    });
  assert.equal(verdict(stall("1:00.000") + rotation), "pass");
  assert.equal(
    verdict(stall("1:00.000") + stall("2:00.000") + rotation),
    "fail",
  );
});

test("a watchdog resume earns no rotation credit", () => {
  assert.equal(
    verdict(line({ trigger: "watchdog", handover: "resumed" })),
    "fail",
  );
});

test("a GoAway nobody replaced fails, and no GoAway at all is inconclusive", () => {
  assert.equal(verdict("", { goAway: true }), "fail");
  assert.equal(verdict("", { goAway: false }), "inconclusive");
  assert.equal(
    verdict(line({ trigger: "watchdog", handover: "resumed" }), {
      goAway: false,
    }),
    "inconclusive",
  );
});

test("a failure outranks an absent GoAway", () => {
  const log = line({
    trigger: "close",
    handover: "degraded",
    fallback: "no_handle",
  });
  assert.equal(verdict(log, { goAway: false }), "fail");
});

test("an expired GoAway fails", () => {
  assert.equal(
    verdict(line({ trigger: "goaway", handover: "resumed" }), {
      expired: true,
    }),
    "fail",
  );
});

test("before the end nothing is decided unless something has failed", () => {
  const early = judge.judgeSoak({
    replacements: [],
    goAway: true,
    expired: false,
    final: false,
  });
  assert.equal(early.verdict, "pending");
  const failed = judge.judgeSoak({
    replacements: parse(
      line({ trigger: "close", handover: "degraded", fallback: "no_handle" }),
    ),
    goAway: false,
    expired: false,
    final: false,
  });
  assert.equal(failed.verdict, "fail");
});

test("a room name with spaces is read whole", () => {
  const room = "interview browser 1";
  const log = line({ trigger: "goaway", handover: "resumed" }, room);
  assert.equal(parse(log, room).length, 1);
  assert.equal(parse(log, "interview").length, 0);
});

test("a refused handle after a reply timeout is a defect, not a stall", () => {
  const result = judge.judgeSoak({
    replacements: parse(
      line({
        trigger: "watchdog",
        handover: "degraded",
        fallback: "resume_refused",
      }) + line({ trigger: "goaway", handover: "resumed", at: "3:00.000" }),
    ),
    goAway: true,
    expired: false,
  });
  assert.equal(result.verdict, "fail");
  assert.match(result.reasons[0], /fallback=resume_refused/);
});

test("a stall before the first checkpoint is tolerated like a young socket", () => {
  const log =
    line({ trigger: "watchdog", handover: "degraded", fallback: "no_handle" }) +
    line({ trigger: "goaway", handover: "resumed", at: "3:00.000" });
  assert.equal(verdict(log), "pass");
});

test("the advisory counts only in its own room", () => {
  const mine = `Gemini requested a transport restart in 50s; room=${ROOM}\n`;
  const other = "Gemini requested a transport restart in 50s; room=other\n";
  const needle = "Gemini requested a transport restart in";
  assert.equal(judge.roomLogged(other, needle, ROOM), false);
  assert.equal(judge.roomLogged(other + mine, needle, ROOM), true);
  assert.equal(judge.roomLogged(mine.trimEnd(), needle, ROOM), false);
});
