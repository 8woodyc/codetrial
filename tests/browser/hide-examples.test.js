// Run with: node --test tests/browser/hide-examples.test.js
//
// Hiding the worked examples is a preflight choice kept between visits. The
// markup half is covered in render.test.js; this drives the real page, because
// what can break is the wiring between the checkbox, storage and the Problem
// tab, and a source-text match cannot tell wired from merely written.

import { after, before, test } from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_RUNTIME_CONFIG,
  launchChromium,
  startStaticServer,
} from "./source.js";
import { SHOW_SOURCES_KEY } from "../../web/lib.js";

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

const problemTab = (page) =>
  page.evaluate(() => ({
    hidden: document.querySelector("#hide-examples").checked,
    examples: document.querySelectorAll("#problem-panel .examples").length,
    brief:
      document
        .querySelector("#problem-panel .problem-detail > .problem-brief")
        ?.textContent.trim() ?? "",
  }));

test("the worked examples can be hidden, and stay hidden on the next visit", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.goto(`${base}/interview.html?problem=chargeback-pair-match`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForSelector("#problem-panel .examples");

    const first = await problemTab(page);
    assert.equal(first.hidden, false, "shown unless the candidate asks");
    assert.equal(first.examples, 1);
    assert.notEqual(first.brief, "");

    await page.check("#hide-examples");
    const hidden = await problemTab(page);
    assert.equal(hidden.examples, 0, "ticking removes the examples at once");
    assert.equal(hidden.brief, first.brief, "the scenario stays on screen");

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("#problem-panel .problem-detail");
    const returning = await problemTab(page);
    assert.equal(returning.hidden, true, "the choice is remembered");
    assert.equal(returning.examples, 0, "and applied before the first render");

    await page.uncheck("#hide-examples");
    assert.equal(
      (await problemTab(page)).examples,
      1,
      "unticking brings them back",
    );
  } finally {
    await page.close();
  }
});

test("the interview hides the published problem name when the toggle is off", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.goto(`${base}/interview.html?problem=chargeback-pair-match`, {
      waitUntil: "domcontentloaded",
    });
    await page.evaluate(
      (key) => localStorage.setItem(key, "0"),
      SHOW_SOURCES_KEY,
    );
    await page.reload({ waitUntil: "domcontentloaded" });
    await page
      .locator("#problem-panel .problem-detail")
      .waitFor({ state: "attached" });
    assert.doesNotMatch(
      await page.locator("#problem-panel").textContent(),
      /LeetCode:/,
    );
  } finally {
    await page.close();
  }
});

test("the interview shows the published problem name when the toggle is on", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.goto(`${base}/interview.html?problem=chargeback-pair-match`, {
      waitUntil: "domcontentloaded",
    });
    await page.evaluate(
      (key) => localStorage.setItem(key, "1"),
      SHOW_SOURCES_KEY,
    );
    await page.reload({ waitUntil: "domcontentloaded" });
    await page
      .locator("#problem-panel .problem-detail")
      .waitFor({ state: "attached" });
    assert.match(
      await page.locator("#problem-panel").textContent(),
      /LeetCode: 1\. Two Sum/,
    );
  } finally {
    await page.close();
  }
});

// The Add a case placeholder is the judge's first input, a worked case in its
// own right, so hiding the examples has to hide it too.
test("hiding the examples also hides the Add a case placeholder", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  const placeholder = () =>
    page.evaluate(
      () => document.querySelector("#candidate-case-input").placeholder,
    );
  try {
    await page.goto(`${base}/interview.html?problem=chargeback-pair-match`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(
      () => document.querySelector("#candidate-case-input").placeholder !== "",
    );
    const shown = await placeholder();

    await page.check("#hide-examples");
    assert.equal(await placeholder(), "", "ticking clears it at once");
    await page.uncheck("#hide-examples");
    assert.equal(await placeholder(), shown, "unticking brings it back");
    await page.check("#hide-examples");

    // Hidden before the judge arrives: the placeholder must stay empty once
    // it does, not be written over by the load.
    const judge = page.waitForResponse((response) =>
      response.url().includes("/judges/chargeback-pair-match.json"),
    );
    await page.reload({ waitUntil: "domcontentloaded" });
    await (await judge).finished();
    await page.evaluate(() => new Promise((done) => setTimeout(done, 200)));
    assert.equal(await placeholder(), "", "a returning candidate sees none");
    await page.uncheck("#hide-examples");
    assert.equal(await placeholder(), shown);
  } finally {
    await page.close();
  }
});

// Blocked site data must mean "shown", not a page that never renders.
test("the examples still render when storage cannot be reached", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.addInitScript(() => {
      Object.defineProperty(window, "localStorage", {
        get() {
          throw new DOMException("blocked", "SecurityError");
        },
      });
    });
    await page.goto(`${base}/interview.html?problem=chargeback-pair-match`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForSelector("#problem-panel .examples");
    await page.check("#hide-examples");
    assert.equal((await problemTab(page)).examples, 0);
  } finally {
    await page.close();
  }
});
