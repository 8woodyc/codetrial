import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import {
  launchChromium,
  startStaticServer,
  DEFAULT_RUNTIME_CONFIG,
} from "./source.js";

import { compilerArguments } from "../../web/runners.js";

let browser, server, base;
before(async () => {
  browser = await launchChromium();
  ({ server, base } = await startStaticServer({
    runtimeConfig: DEFAULT_RUNTIME_CONFIG,
  }));
});
after(async () => {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
});

test("a custom task executes Python practice cases without loading a catalog judge", async (t) => {
  if (!browser) {
    t.skip("playwright chromium unavailable");
    return;
  }
  const page = await browser.newPage();
  const catalogRequests = [];
  page.on("request", (request) => {
    if (
      request.url().includes("/judges/") ||
      request.url().includes("/problems/")
    )
      catalogRequests.push(request.url());
  });
  await page.route("**/task-runner-test", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: "<!doctype html><title>Task runner test</title>",
    }),
  );
  try {
    await page.goto(`${base}/task-runner-test`);
    const result = await page.evaluate(async () => {
      const { runBrowserTests } = await import("/runners.js");
      return runBrowserTests(
        "custom-unit-conversion",
        "def convert_units(value):\n    return value * 2\n",
        "python",
        null,
        [],
        {
          kind: "function",
          entry: "convert_units",
          checker: "exact",
          cases: [
            { label: "zero", input: [0], expected: 0 },
            { label: "positive", input: [3], expected: 6 },
          ],
        },
      );
    });
    assert.equal(result.total, 2, JSON.stringify(result));
    assert.equal(result.passed, 2, JSON.stringify(result));
    assert.deepEqual(catalogRequests, []);
  } finally {
    await page.close();
  }
});

test("a task lowers the compiler optimization and can never raise it", () => {
  const arguments_ = (language, spec) => compilerArguments(language, spec);
  assert.equal(arguments_("c", {}), "-O2 -std=c17");
  assert.equal(arguments_("c", null), "-O2 -std=c17");
  assert.equal(arguments_("c", { optimization: "O1" }), "-O1 -std=c17");
  assert.equal(arguments_("c", { optimization: "O0" }), "-O0 -std=c17");
  assert.equal(arguments_("cpp", { optimization: "O1" }), "-O1 -std=c++20");
  // Only the two lower levels are read, and nothing else reaches the flags.
  for (const level of ["O2", "O3", "-O3 -fno-x", "o1", "", 1, null])
    assert.equal(
      arguments_("c", { optimization: level }),
      "-O2 -std=c17",
      level,
    );
  // Java takes no such flag.
  assert.equal(arguments_("java", { optimization: "O1" }), "");
});
