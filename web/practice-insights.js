import { reportContractStatus, reportTopics } from "./lib.js";

export function availableTopics(problems) {
  return [...new Set(problems.flatMap((problem) => problem.topics ?? []))].sort(
    (left, right) => left.localeCompare(right),
  );
}

export function filterProblems(
  problems,
  {
    difficulties = new Set(),
    topic = "",
    query = "",
    searchBy = "scenario",
  } = {},
) {
  const needle = query.trim().toLowerCase();
  return problems.filter(
    (problem) =>
      (!difficulties.size || difficulties.has(problem.difficulty)) &&
      (!topic || problem.topics.includes(topic)) &&
      (!needle ||
        (
          (searchBy === "leetcode" ? problem.leetcodeTitle : problem.title) ??
          ""
        )
          .toLowerCase()
          .includes(needle)),
  );
}

const ASSESSED = new Set(["HIRE", "NO_HIRE"]);

export function recentPerformance(reports, limit = 8) {
  const attempts = reports
    .filter(
      (entry) =>
        Number.isFinite(entry.at) &&
        !entry.report?.incomplete &&
        ASSESSED.has(entry.report?.decision) &&
        reportContractStatus(entry.report) !== "unsupported",
    )
    .sort((left, right) => right.at - left.at)
    .slice(0, limit);
  if (!attempts.length) return null;

  const passes = attempts.filter(
    (entry) => entry.report.decision === "HIRE",
  ).length;
  const topicResults = new Map();
  // Newest first, so the attempt that creates a topic's row is its latest.
  for (const entry of attempts) {
    for (const topic of new Set(reportTopics(entry.report) ?? [])) {
      const result = topicResults.get(topic) ?? {
        passes: 0,
        misses: 0,
        lastAt: entry.at,
      };
      result[entry.report.decision === "HIRE" ? "passes" : "misses"] += 1;
      topicResults.set(topic, result);
    }
  }
  const weakTopicDetails = [...topicResults]
    .filter(([, result]) => result.misses > result.passes)
    .sort(
      ([leftTopic, left], [rightTopic, right]) =>
        right.misses - right.passes - (left.misses - left.passes) ||
        leftTopic.localeCompare(rightTopic),
    )
    .map(([topic, result]) => ({ topic, ...result }));
  const weakTopics = weakTopicDetails.map(({ topic }) => topic);
  const latestDecision = attempts[0].report.decision;
  const streak = attempts.findIndex(
    (entry) => entry.report.decision !== latestDecision,
  );

  return {
    attempts: attempts.length,
    legacyAttempts: attempts.filter(
      (entry) => reportContractStatus(entry.report) === "legacy",
    ).length,
    passes,
    misses: attempts.length - passes,
    passRate: Math.round((passes / attempts.length) * 100),
    streak: streak === -1 ? attempts.length : streak,
    latestDecision,
    weakTopics,
    weakTopicDetails,
  };
}
