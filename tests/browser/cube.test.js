import test from "node:test";
import assert from "node:assert/strict";
import {
  MAX_MOVES,
  PLACES,
  apply,
  formatMove,
  net,
  normalized,
  parseAlgorithm,
  positionCode,
  readPlaces,
  solved,
  stateFromCode,
  turn,
} from "../../web/cube.js";
import { read } from "./source.js";

// The starter's own tables, from problem-bank/task-examples/rubik.json: face 0
// is R, 1 is B and 2 is D, and a position is a place per cubie and a twist.
const bank = JSON.parse(read("problem-bank/task-examples/rubik.json"));
const MAPS = [...bank.problem.starterCode.c.matchAll(/"(\d{14})"/g)].map(
  (match) => match[1],
);

function tableTurn(position, face) {
  const map = MAPS[face];
  return {
    p: position.p.map((_, i) => position.p[Number(map[i])]),
    o: position.o.map(
      (_, i) => (position.o[Number(map[i])] + Number(map[i + 7])) % 3,
    ),
  };
}
const tableCode = ({ p, o }) =>
  p.map((n) => n + 1).join("") + o.map((n) => n + 1).join("");

// A small seeded generator, so a failure names the same sequence every run.
function random(seed) {
  let state = seed;
  return () => {
    state = (state * 1664525 + 1013904223) % 2 ** 32;
    return state / 2 ** 32;
  };
}

test("the tables are the ones in the starter", () => {
  assert.equal(MAPS.length, 3);
  assert.equal(positionCode(solved()), "12345671111111");
});

test("every turn of the right, back and down faces matches the solver's tables", () => {
  const next = random(7);
  const faces = ["R", "B", "D"];
  for (let run = 0; run < 300; run += 1) {
    let state = solved();
    let position = { p: [0, 1, 2, 3, 4, 5, 6], o: [0, 0, 0, 0, 0, 0, 0] };
    const written = [];
    for (let step = 0; step < 1 + Math.floor(next() * 10); step += 1) {
      const face = Math.floor(next() * 3);
      const quarters = 1 + Math.floor(next() * 3);
      state = turn(state, faces[face], quarters);
      for (let again = 0; again < quarters; again += 1)
        position = tableTurn(position, face);
      written.push(formatMove({ face: faces[face], amount: quarters }));
    }
    assert.equal(positionCode(state), tableCode(position), written.join(" "));
  }
});

test("a face and the one opposite it turn the cube alike, once it is held by its fixed corner", () => {
  // Turning the left layer clockwise as seen from the left moves it, against
  // the right layer, as turning the right layer clockwise as seen from the
  // right does: the two differ by a turn of the whole cube.
  const next = random(11);
  const opposite = { L: "R", U: "D", F: "B" };
  for (let run = 0; run < 200; run += 1) {
    let state = solved();
    for (let step = 0; step < 6; step += 1)
      state = turn(
        state,
        ["R", "B", "D"][Math.floor(next() * 3)],
        1 + Math.floor(next() * 3),
      );
    for (const [face, other] of Object.entries(opposite))
      for (const quarters of [1, 2, 3]) {
        assert.equal(
          positionCode(turn(state, face, quarters)),
          positionCode(turn(state, other, quarters)),
          `${face}${quarters}`,
        );
      }
  }
});

test("turns compose: four quarters are none, two quarters are a half, and a turn undoes itself", () => {
  const state = apply(solved(), parseAlgorithm("R D B' R2"));
  for (const face of "URFDLB") {
    assert.equal(positionCode(turn(state, face, 4)), positionCode(state), face);
    assert.equal(
      positionCode(turn(turn(state, face, 1), face, 1)),
      positionCode(turn(state, face, 2)),
      face,
    );
    assert.equal(
      positionCode(turn(turn(state, face, 1), face, -1)),
      positionCode(state),
      face,
    );
  }
  // The input is never changed.
  assert.equal(positionCode(solved()), "12345671111111");
});

test("a code builds the position it names, and the position reads back as the code", () => {
  const next = random(23);
  for (let run = 0; run < 200; run += 1) {
    let state = solved();
    for (let step = 0; step < 8; step += 1)
      state = turn(
        state,
        "URFDLB"[Math.floor(next() * 6)],
        1 + Math.floor(next() * 3),
      );
    const code = positionCode(state);
    const built = stateFromCode(code);
    assert.equal(positionCode(built), code);
    // The same cube, held by its fixed corner.
    const byCubie = (pieces) =>
      JSON.stringify(
        pieces
          .map((piece) => [piece.id, piece.position, piece.stickers])
          .sort(([a], [b]) => a - b),
      );
    assert.equal(byCubie(normalized(state)), byCubie(built));
  }
});

test("every public case of the rubik task is a position the cube can show", () => {
  const cases = bank.judge.cases.map((row) => row.input[0]);
  assert.ok(cases.length >= 10);
  for (const code of cases)
    assert.equal(positionCode(stateFromCode(code)), code);
});

test("a code that is not a position is refused for what is wrong with it", () => {
  for (const [code, message] of [
    ["1234567111111", /14 digits/],
    ["12345671111118", /14 digits/],
    ["11345671111111", /different cubie/],
    ["12345671111112", /multiple of three/],
    [undefined, /14 digits/],
  ])
    assert.throws(() => stateFromCode(code), message, String(code));
});

test("the code lists cubie and twist for each place", () => {
  assert.deepEqual(
    readPlaces(apply(solved(), parseAlgorithm("R"))).map(
      ({ place, cubie, twist }) => `${place}:${cubie}:${twist}`,
    ),
    [
      "URF:2:1",
      "DRF:5:2",
      "DFL:3:0",
      "URB:1:2",
      "DRB:4:1",
      "DBL:6:0",
      "UBL:7:0",
    ],
  );
  assert.deepEqual(
    PLACES.map(({ name }) => name),
    ["URF", "DRF", "DFL", "URB", "DRB", "DBL", "UBL"],
  );
});

test("the notation reads moves, groups, commutators and conjugates", () => {
  const written = (text) => parseAlgorithm(text).map(formatMove).join(" ");
  assert.equal(written("R U R' U'"), "R U R' U'");
  assert.equal(written("  r"[0] === " " ? "R2 D2'" : ""), "R2 D2");
  assert.equal(written("(R D)2"), "R D R D");
  assert.equal(written("(R D)'"), "D' R'");
  assert.equal(written("[R, D]"), "R D R' D'");
  assert.equal(written("[R: D]"), "R D R'");
  assert.equal(written("R: D"), "R D R'");
  assert.equal(written("[R: [D, B]]"), "R D B D' B' R'");
  assert.equal(written("R’ D2"), "R' D2");
  assert.equal(written(""), "");
  // The same algorithm, written two ways, is the same position.
  assert.equal(
    positionCode(apply(solved(), parseAlgorithm("[R, D]"))),
    positionCode(apply(solved(), parseAlgorithm("R D R' D'"))),
  );
});

test("an algorithm that cannot be read says what could not be", () => {
  for (const [text, message] of [
    ["X", /Cannot read "X"/],
    ["R3", /Cannot read "3"/],
    ["(R D", /"\(" is not closed/],
    ["[R, D", /"\[" is not closed/],
    ["R)", /Unexpected "\)"/],
    ["R, D", /Unexpected ","/],
    [`(R D)${MAX_MOVES}`, /More than/],
    [`${"R ".repeat(MAX_MOVES + 1)}`, /More than/],
    [undefined, /Enter an algorithm/],
  ])
    assert.throws(() => parseAlgorithm(text), message, String(text));
});

test("each face of a net reads as seen from outside", () => {
  const faces = net(solved());
  for (const [face, colour] of Object.entries({
    U: "yellow",
    D: "white",
    F: "green",
    B: "blue",
    R: "red",
    L: "orange",
  }))
    assert.deepEqual(
      faces[face],
      [
        [colour, colour],
        [colour, colour],
      ],
      face,
    );
  // A clockwise turn of the right face carries the front up to the top, the
  // top back, the back down and the bottom to the front.
  const turned = net(turn(solved(), "R"));
  assert.deepEqual(turned.U, [
    ["yellow", "green"],
    ["yellow", "green"],
  ]);
  assert.deepEqual(turned.F, [
    ["green", "white"],
    ["green", "white"],
  ]);
  assert.deepEqual(turned.D, [
    ["white", "blue"],
    ["white", "blue"],
  ]);
  assert.deepEqual(turned.B, [
    ["yellow", "blue"],
    ["yellow", "blue"],
  ]);
  assert.deepEqual(turned.R, [
    ["red", "red"],
    ["red", "red"],
  ]);
  assert.deepEqual(turned.L, [
    ["orange", "orange"],
    ["orange", "orange"],
  ]);
});
