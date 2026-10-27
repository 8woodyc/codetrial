import { loadPageMap } from "./problem-data.js";
import { showOriginalProblem } from "./render.js";

/// The lookup may outlive the report being read. Check the caller's current
/// selection after it settles, before touching the report's reserved line.
export async function loadOriginalProblem(
  container,
  page,
  isCurrent = () => true,
) {
  try {
    const pages = await loadPageMap();
    if (isCurrent()) showOriginalProblem(container, pages, page);
  } catch {
    // A missing optional reference must not prevent reading the report.
  }
}
