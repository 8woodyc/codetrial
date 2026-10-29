import { after, before, test } from "node:test";
import assert from "node:assert/strict";

import {
  closeSetupCheck,
  DEFAULT_RUNTIME_CONFIG,
  launchChromium,
  startStaticServer,
} from "./source.js";

let browser = null;
let server = null;
let base = "";

before(async () => {
  browser = await launchChromium();
  if (!browser) return;
  ({ server, base } = await startStaticServer({
    runtimeConfig: DEFAULT_RUNTIME_CONFIG,
  }));
});

after(async () => {
  await browser?.close();
  await new Promise((closed) => (server ? server.close(closed) : closed()));
});

test("ending an interview shows the original problem below the report title", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  page.on("dialog", (dialog) => dialog.accept());
  try {
    await page.goto(`${base}/interview.html?problem=chargeback-pair-match`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(
      () => document.querySelector("#candidate-case-input").placeholder !== "",
    );
    await closeSetupCheck(page);
    await page.locator("#end").click();
    const link = page.locator("#report-modal h2 + .report-original-problem a");
    await link.waitFor();
    assert.equal(await link.textContent(), "1. Two Sum");
    assert.equal(
      await link.getAttribute("href"),
      "https://leetcode.com/problems/two-sum/",
    );
  } finally {
    await page.close();
  }
});

test("a late original-problem lookup cannot put recording A's link on B", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  const pendingMap = Promise.withResolvers();
  await page.route("**/problem-pages.json", async (route) => {
    await pendingMap.promise;
    await route.continue();
  });
  await page.route("**/api/recordings", (route) =>
    route.fulfill({
      json: {
        recordings: ["A", "B"].map((recordingId) => ({
          recordingId,
          createdAt: 1700000000,
          state: "ready",
        })),
      },
    }),
  );
  await page.route(/\/api\/recordings\/[AB]$/, (route) => {
    const id = route.request().url().split("/").at(-1);
    return route.fulfill({
      json: { recordingId: id, interviewId: id, state: "ready" },
    });
  });
  await page.route("**/api/recordings/*/events?*", (route) =>
    route.fulfill({ json: { events: [] } }),
  );
  await page.route("**/api/reports", (route) =>
    route.fulfill({
      json: {
        reports: [
          ["A", "chargeback-pair-match", "Chargeback Pair Match"],
          ["B", "template-delimiter-audit", "Template Delimiter Audit"],
        ].map(([interviewId, problemId, problemTitle]) => ({
          payload: {
            interviewId,
            problemId,
            problemTitle,
            report: { decision: "HIRE", codingScore: 80 },
          },
        })),
      },
    }),
  );
  try {
    await page.goto(`${base}/replay.html`, { waitUntil: "domcontentloaded" });
    const mapRequested = page.waitForRequest("**/problem-pages.json");
    await page.locator('.replay-item[data-recording="A"]').click();
    await mapRequested;
    await page.locator("#replay-report h2").waitFor();
    await page.locator('.replay-item[data-recording="B"]').click();
    await page.waitForFunction(() =>
      document
        .querySelector("#replay-report")
        .textContent.includes("Template Delimiter Audit"),
    );
    await page.evaluate(() => {
      window.reportLinksAdded = [];
      // Both callbacks share the cached lookup. Record even a stale link
      // replaced by B's callback in the same turn of the event loop.
      new MutationObserver((records) => {
        for (const record of records) {
          for (const node of record.addedNodes) {
            if (node.nodeName === "A") window.reportLinksAdded.push(node.href);
          }
        }
      }).observe(document.querySelector("#replay-report"), {
        childList: true,
        subtree: true,
      });
    });
    pendingMap.resolve();
    const link = page.locator("#replay-report .report-original-problem a");
    await link.waitFor();
    assert.equal(await link.textContent(), "20. Valid Parentheses");
    assert.equal(
      await link.getAttribute("href"),
      "https://leetcode.com/problems/valid-parentheses/",
    );
    assert.deepEqual(await page.evaluate(() => window.reportLinksAdded), [
      "https://leetcode.com/problems/valid-parentheses/",
    ]);
  } finally {
    pendingMap.resolve();
    await page.close();
  }
});
