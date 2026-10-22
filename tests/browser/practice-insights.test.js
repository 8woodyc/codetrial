import { test } from "node:test";
import assert from "node:assert/strict";

import {
  availableTopics,
  filterProblems,
  recentPerformance,
} from "../../web/practice-insights.js";

import { ACTIVE_CONTRACT, reportContractStatus } from "../../web/lib.js";

const problems = [
  { id: "a", difficulty: "Easy", topics: ["Array", "Hash Table"] },
  { id: "b", difficulty: "Medium", topics: ["Array", "Sorting"] },
  { id: "c", difficulty: "Hard", topics: ["Graph"] },
];

const report = (problemId, at, decision) => ({
  problemId,
  at,
  report: {
    decision,
    topics: [
      ...(problems.find((problem) => problem.id === problemId)?.topics ?? []),
    ],
  },
});

test("topics are unique and sorted", () => {
  assert.deepEqual(availableTopics(problems), [
    "Array",
    "Graph",
    "Hash Table",
    "Sorting",
  ]);
});

test("difficulty and topic filters combine", () => {
  assert.deepEqual(
    filterProblems(problems, {
      difficulties: new Set(["Medium", "Hard"]),
      topic: "Array",
    }).map((problem) => problem.id),
    ["b"],
  );
});

test("recent performance summarizes assessed reports newest first", () => {
  const reports = [
    report("a", 10, "NO_HIRE"),
    report("b", 30, "HIRE"),
    report("a", 20, "NO_HIRE"),
    report("c", 40, "HIRE"),
  ];

  assert.deepEqual(recentPerformance(reports, 3), {
    attempts: 3,
    legacyAttempts: 3,
    passes: 2,
    misses: 1,
    passRate: 67,
    streak: 2,
    latestDecision: "HIRE",
    weakTopics: ["Hash Table"],
    weakTopicDetails: [
      { topic: "Hash Table", misses: 1, passes: 0, lastAt: 20 },
    ],
  });
});

test("recent performance ignores unscored and undated entries", () => {
  assert.equal(
    recentPerformance([
      { problemId: "a", at: null, report: { decision: "NO_HIRE" } },
      { problemId: "b", at: 20, report: { decision: "PENDING" } },
    ]),
    null,
  );
});

test("recent performance ignores incomplete reports with a decision", () => {
  assert.equal(
    recentPerformance([
      {
        problemId: "a",
        at: 20,
        report: { incomplete: true, decision: "HIRE", topics: ["Array"] },
      },
    ]),
    null,
  );
});

test("topics with more misses than passes are highlighted", () => {
  const snapshot = recentPerformance([
    report("a", 30, "NO_HIRE"),
    report("a", 20, "NO_HIRE"),
    report("b", 10, "HIRE"),
  ]);

  assert.deepEqual(snapshot.weakTopics, ["Hash Table", "Array"]);
});

test("recent performance ignores malformed report topics", () => {
  const malformed = report("a", 20, "NO_HIRE");
  malformed.report.topics = 5;

  assert.deepEqual(recentPerformance([malformed]).weakTopics, []);
});

test("recent performance counts each normalized topic once per report", () => {
  const first = report("a", 20, "NO_HIRE");
  first.report.topics = ["Array", "Array", 5, "Hash Table"];
  const second = report("b", 10, "NO_HIRE");
  second.report.topics = ["Array"];

  assert.deepEqual(recentPerformance([first, second]).weakTopics, [
    "Array",
    "Hash Table",
  ]);
});

const versionedReport = (at, decision, contract = ACTIVE_CONTRACT) => {
  const entry = report("a", at, decision);
  entry.report.interviewContract = { ...contract };
  return entry;
};

test("recent performance excludes future and malformed contracts before limiting", () => {
  const future = versionedReport(40, "HIRE", {
    ...ACTIVE_CONTRACT,
    bundleVersion: ACTIVE_CONTRACT.bundleVersion + 1,
  });
  const malformed = versionedReport(30, "HIRE", {
    ...ACTIVE_CONTRACT,
    extra: 1,
  });
  const supported = versionedReport(20, "NO_HIRE");
  const snapshot = recentPerformance([future, malformed, supported], 1);
  assert.equal(snapshot.attempts, 1);
  assert.equal(snapshot.passes, 0);
  assert.equal(snapshot.legacyAttempts, 0);
  assert.equal(snapshot.latestDecision, "NO_HIRE");
  assert.equal(recentPerformance([future, malformed]), null);
});

test("recent performance counts legacy provenance only inside the window", () => {
  const legacy = report("a", 20, "HIRE");
  const supported = versionedReport(30, "NO_HIRE");
  assert.equal(recentPerformance([legacy]).legacyAttempts, 1);
  assert.equal(recentPerformance([legacy, supported], 1).legacyAttempts, 0);
  assert.equal(recentPerformance([legacy, supported], 2).legacyAttempts, 1);
});

test("contract classification shares the report compatibility policy", () => {
  assert.equal(reportContractStatus({}), "legacy");
  assert.equal(
    reportContractStatus({ interviewContract: ACTIVE_CONTRACT }),
    "supported",
  );
  for (const contract of [
    null,
    [],
    { ...ACTIVE_CONTRACT, extra: 1 },
    { ...ACTIVE_CONTRACT, rubricVersion: ACTIVE_CONTRACT.rubricVersion + 1 },
    { ...ACTIVE_CONTRACT, reportSchemaVersion: 999 },
    { ...ACTIVE_CONTRACT, bundleVersion: 3 },
    {
      ...ACTIVE_CONTRACT,
      livePromptVersion: ACTIVE_CONTRACT.livePromptVersion + 1,
    },
    {
      ...ACTIVE_CONTRACT,
      reportPromptVersion: ACTIVE_CONTRACT.reportPromptVersion + 1,
    },
  ]) {
    assert.equal(
      reportContractStatus({ interviewContract: contract }),
      "unsupported",
    );
  }
});

test("weak topic details retain ranking, per-topic counts and latest dates in the window", () => {
  const duplicate = report("a", 30, "NO_HIRE");
  duplicate.report.topics.push("Hash Table");
  const snapshot = recentPerformance(
    [
      report("a", 1, "NO_HIRE"),
      report("b", 20, "HIRE"),
      duplicate,
      report("a", 10, "NO_HIRE"),
    ],
    3,
  );
  assert.deepEqual(snapshot.weakTopicDetails, [
    { topic: "Hash Table", misses: 2, passes: 0, lastAt: 30 },
    { topic: "Array", misses: 2, passes: 1, lastAt: 30 },
  ]);
  assert.deepEqual(
    snapshot.weakTopicDetails.map(({ topic }) => topic),
    snapshot.weakTopics,
  );
});

test("weak topic details exclude incompatible, incomplete and undated evidence", () => {
  const future = versionedReport(40, "HIRE", {
    ...ACTIVE_CONTRACT,
    bundleVersion: ACTIVE_CONTRACT.bundleVersion + 1,
  });
  const incomplete = report("a", 50, "HIRE");
  incomplete.report.incomplete = true;
  const undated = report("a", null, "HIRE");
  assert.deepEqual(
    recentPerformance(
      [future, incomplete, undated, report("a", 20, "NO_HIRE")],
      1,
    ).weakTopicDetails,
    [
      { topic: "Array", misses: 1, passes: 0, lastAt: 20 },
      { topic: "Hash Table", misses: 1, passes: 0, lastAt: 20 },
    ],
  );
});
