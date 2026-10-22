import { test } from "node:test";
import assert from "node:assert/strict";
import {
  parsePracticeList,
  pickPracticeProblem,
} from "../../web/practice-list.js";

const bank = [
  { id: "easy", difficulty: "Easy" },
  { id: "medium", difficulty: "Medium" },
  { id: "hard", difficulty: "Hard" },
  { id: "unmarked", difficulty: "Easy" },
];
const validIds = new Set(bank.map((problem) => problem.id));

test("saved practice IDs discard duplicates, retired IDs and malformed values", () => {
  assert.deepEqual(
    [
      ...parsePracticeList(
        '["easy","retired","easy",null,42,"hard"]',
        validIds,
      ),
    ],
    ["easy", "hard"],
  );
  for (const stored of [null, "{", "{}", '"easy"', "42", "null"])
    assert.equal(parsePracticeList(stored, validIds).size, 0);
});

test("practice draws allocate equal intervals only to marked problems at every difficulty", () => {
  const ids = new Set(["hard", "easy", "medium", "retired"]);
  const counts = new Map();
  for (let sample = 0; sample < 300; sample++) {
    const picked = pickPracticeProblem(bank, ids, () => (sample + 0.5) / 300);
    counts.set(picked.id, (counts.get(picked.id) ?? 0) + 1);
  }
  assert.deepEqual(
    [...counts],
    [
      ["easy", 100],
      ["medium", 100],
      ["hard", 100],
    ],
  );
});

test("practice draws exclude the current problem only when another marked problem exists", () => {
  const ids = new Set(["easy", "hard"]);
  for (const avoid of ["easy", "hard"]) {
    for (const roll of [0, 0.5, 0.999999]) {
      const picked = pickPracticeProblem(bank, ids, () => roll, avoid);
      assert.equal(picked.id, avoid === "easy" ? "hard" : "easy");
    }
  }
  assert.equal(
    pickPracticeProblem(bank, new Set(["hard"]), () => 0, "hard").id,
    "hard",
  );
  for (const empty of [new Set(), new Set(["retired"])])
    assert.equal(
      pickPracticeProblem(bank, empty, () => 0),
      null,
    );
});
