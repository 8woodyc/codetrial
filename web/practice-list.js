export const PRACTICE_LIST_KEY = "codetrial.practiceList";

export function parsePracticeList(stored, validIds) {
  try {
    const ids = JSON.parse(stored);
    return new Set(
      Array.isArray(ids) ? ids.filter((id) => validIds.has(id)) : [],
    );
  } catch {
    return new Set();
  }
}

// Filter the bank rather than the saved array so duplicate or retired IDs
// cannot bias a draw. Difficulty and topic filters do not narrow this pool.
export function pickPracticeProblem(
  problems,
  ids,
  random = Math.random,
  avoid,
) {
  const pool = problems.filter((problem) => ids.has(problem.id));
  const alternatives = pool.filter((problem) => problem.id !== avoid);
  const choices = alternatives.length ? alternatives : pool;
  return choices[Math.floor(random() * choices.length)] ?? null;
}
