import { test } from "node:test";
import assert from "node:assert/strict";
import { downloadMarkdown, reportFilename } from "../../web/download.js";

test("report filenames use local timestamps and safe scenario names", () => {
  assert.equal(
    reportFilename("scenario/../name?", new Date(2026, 0, 2, 1, 30)),
    "interview-report-scenario----name--2026-01-02-013000.md",
  );
  assert.equal(
    reportFilename("", new Date(2026, 10, 12, 23, 30)),
    "interview-report-past-interview-2026-11-12-233000.md",
  );
});

test("report filenames distinguish times on the same local calendar day", () => {
  assert.equal(
    reportFilename("two-sum", new Date(2026, 9, 14, 1, 2, 3)),
    "interview-report-two-sum-2026-10-14-010203.md",
  );
  assert.equal(
    reportFilename("two-sum", new Date(2026, 9, 14, 15, 30, 0)),
    "interview-report-two-sum-2026-10-14-153000.md",
  );
});

test("report filenames follow the local midnight boundary", () => {
  assert.equal(
    reportFilename("two-sum", new Date(2026, 9, 14, 23, 59, 59)),
    "interview-report-two-sum-2026-10-14-235959.md",
  );
  assert.equal(
    reportFilename("two-sum", new Date(2026, 9, 15, 0, 0, 0)),
    "interview-report-two-sum-2026-10-15-000000.md",
  );
});

test("markdown downloads release their anchor and object URL after the click", async (t) => {
  const events = [];
  let blob;
  const anchor = {
    click() {
      events.push(["click", this.href, this.download]);
    },
    remove() {
      events.push(["remove"]);
    },
  };
  const previous = Object.getOwnPropertyDescriptor(globalThis, "document");
  Object.defineProperty(globalThis, "document", {
    configurable: true,
    value: {
      createElement(tag) {
        assert.equal(tag, "a");
        return anchor;
      },
      body: {
        append(node) {
          assert.equal(node, anchor);
          events.push(["append"]);
        },
      },
    },
  });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, "document", previous);
    else delete globalThis.document;
  });
  t.mock.method(URL, "createObjectURL", (value) => {
    blob = value;
    return "blob:report";
  });
  t.mock.method(URL, "revokeObjectURL", (url) => events.push(["revoke", url]));
  t.mock.timers.enable({ apis: ["setTimeout"] });
  downloadMarkdown("# Saved report\n", "report.md");
  assert.equal(blob.type, "text/markdown");
  assert.equal(await blob.text(), "# Saved report\n");
  assert.deepEqual(events, [
    ["append"],
    ["click", "blob:report", "report.md"],
    ["remove"],
  ]);
  t.mock.timers.runAll();
  assert.deepEqual(events.at(-1), ["revoke", "blob:report"]);
  assert.equal(events.length, 4);
});
