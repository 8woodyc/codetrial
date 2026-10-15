export function reportFilename(problemId, at) {
  const name = problemId.replace(/[^a-zA-Z0-9_-]/g, "-") || "past-interview";
  // Match the report's local timestamp, including interviews near midnight.
  const date = [at.getFullYear(), at.getMonth() + 1, at.getDate()]
    .map((part) => String(part).padStart(2, "0"))
    .join("-");
  const time = [at.getHours(), at.getMinutes(), at.getSeconds()]
    .map((part) => String(part).padStart(2, "0"))
    .join("");
  return `interview-report-${name}-${date}-${time}.md`;
}

export function downloadMarkdown(markdown, filename) {
  const url = URL.createObjectURL(
    new Blob([markdown], { type: "text/markdown" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
