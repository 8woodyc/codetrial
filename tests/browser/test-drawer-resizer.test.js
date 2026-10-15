// Run with: node --test tests/browser/test-drawer-resizer.test.js

import { after, before, test } from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_RUNTIME_CONFIG,
  launchChromium,
  startStaticServer,
} from "./source.js";

const STORAGE_KEY = "codetrial:testDrawerHeight";

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

async function openInterview(page) {
  await page.goto(`${base}/interview.html`, {
    waitUntil: "domcontentloaded",
  });
  await page.waitForFunction(
    () => document.querySelector("#test-drawer-resizer").ariaValueNow !== null,
  );
  await page.evaluate(() => {
    document.querySelector("#audio-check").hidden = true;
  });
}

const layout = (page) =>
  page.evaluate((key) => {
    const rect = (selector) =>
      document.querySelector(selector).getBoundingClientRect();
    const handle = document.querySelector("#test-drawer-resizer");
    const drawerElement = document.querySelector("#test-drawer");
    const drawer = drawerElement.getBoundingClientRect();
    const editorElement = document.querySelector(".editor-stack");
    return {
      editor: editorElement.getBoundingClientRect().height,
      editorMin: Number.parseFloat(getComputedStyle(editorElement).minHeight),
      drawer: drawer.height,
      drawerTop: drawer.top,
      drawerClientHeight: drawerElement.clientHeight,
      drawerScrollHeight: drawerElement.scrollHeight,
      addCaseBottom: rect("#candidate-case").bottom,
      resultsToggleTop: rect("#results-toggle").top,
      resultsToggleBottom: rect("#results-toggle").bottom,
      resultsHidden: document.querySelector("#results-body").hidden,
      drawerBottom: drawer.bottom,
      min: Number(handle.ariaValueMin),
      max: Number(handle.ariaValueMax),
      now: Number(handle.ariaValueNow),
      stored: localStorage.getItem(key),
    };
  }, STORAGE_KEY);

async function carry(page, dy) {
  const box = await page.locator("#test-drawer-resizer").boundingBox();
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x, y + dy, { steps: 4 });
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await page.mouse.up();
}

test("the test drawer resizes vertically and remembers the chosen height", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage({
    viewport: { width: 1280, height: 720 },
  });
  try {
    await openInterview(page);
    const initial = await layout(page);
    assert.equal(initial.stored, null);

    await carry(page, -48);
    const taller = await layout(page);
    assert.equal(taller.drawer, Math.round(initial.drawer) + 48);
    assert.equal(taller.editor, initial.editor - 48);
    assert.equal(taller.now, taller.drawer);
    assert.equal(taller.stored, String(taller.drawer));

    await page.focus("#test-drawer-resizer");
    await page.keyboard.press("End");
    const chosen = await layout(page);
    assert.ok(chosen.drawer > taller.drawer);
    assert.equal(Math.round(Number(chosen.stored)), chosen.drawer);

    await page.click("#results-toggle");
    await page.waitForFunction(
      () =>
        Number(document.querySelector("#test-drawer-resizer").ariaValueNow) ===
        Math.round(
          document.querySelector("#test-drawer").getBoundingClientRect().height,
        ),
    );
    const collapsed = await layout(page);
    assert.equal(collapsed.resultsHidden, true);
    assert.equal(await page.locator("#test-drawer-resizer").isHidden(), true);
    assert.ok(collapsed.drawer < chosen.drawer);
    assert.equal(collapsed.now, Math.round(collapsed.drawer));
    assert.equal(collapsed.stored, chosen.stored);

    await page.click("#results-toggle");
    await page.waitForFunction(
      (height) =>
        document.querySelector("#test-drawer").getBoundingClientRect()
          .height === height &&
        Number(document.querySelector("#test-drawer-resizer").ariaValueNow) ===
          height,
      chosen.drawer,
    );
    const reopened = await layout(page);
    assert.equal(reopened.resultsHidden, false);
    assert.equal(await page.locator("#test-drawer-resizer").isVisible(), true);
    assert.equal(reopened.now, chosen.drawer);

    await page.locator(".editor-toolbar").evaluate((toolbar) => {
      toolbar.style.minHeight = "10rem";
    });
    await page.waitForFunction(
      (height) =>
        document.querySelector("#test-drawer").getBoundingClientRect().height <
        height,
      chosen.drawer,
    );
    const toolbarClamped = await layout(page);
    assert.ok(toolbarClamped.drawer < chosen.drawer);
    assert.ok(toolbarClamped.editor >= toolbarClamped.editorMin - 1);
    assert.equal(toolbarClamped.stored, chosen.stored);

    await page.locator(".editor-toolbar").evaluate((toolbar) => {
      toolbar.style.removeProperty("min-height");
    });
    await page.waitForFunction(
      (height) =>
        document.querySelector("#test-drawer").getBoundingClientRect()
          .height === height,
      chosen.drawer,
    );

    await page.setViewportSize({ width: 1280, height: 640 });
    await page.waitForFunction(
      (height) =>
        document.querySelector("#test-drawer").getBoundingClientRect().height <
        height,
      chosen.drawer,
    );
    const constrained = await layout(page);
    assert.ok(constrained.drawer < chosen.drawer);
    assert.equal(constrained.stored, chosen.stored);

    await page.setViewportSize({ width: 1280, height: 720 });
    await page.waitForFunction(
      (height) =>
        document.querySelector("#test-drawer").getBoundingClientRect()
          .height === height,
      chosen.drawer,
    );
    const restored = await layout(page);
    assert.equal(restored.drawer, chosen.drawer);
    assert.equal(restored.stored, chosen.stored);

    await openInterview(page);
    const reloaded = await layout(page);
    assert.equal(reloaded.drawer, chosen.drawer);
    assert.equal(reloaded.now, chosen.drawer);
  } finally {
    await page.close();
  }
});

test("the horizontal divider supports keys and keeps both panels usable", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage({
    viewport: { width: 1280, height: 720 },
  });
  try {
    await openInterview(page);
    const initial = await layout(page);
    await page.focus("#test-drawer-resizer");

    await page.keyboard.press("ArrowUp");
    assert.equal((await layout(page)).drawer, Math.round(initial.drawer) + 16);
    await page.keyboard.press("ArrowDown");
    assert.equal((await layout(page)).drawer, Math.round(initial.drawer));

    await page.locator("#candidate-case > summary").click();
    await page.focus("#test-drawer-resizer");
    await page.keyboard.press("Home");
    const smallest = await layout(page);
    assert.equal(smallest.drawer, smallest.min);
    assert.ok(smallest.addCaseBottom <= smallest.resultsToggleTop);
    assert.ok(smallest.resultsToggleBottom <= smallest.drawerBottom);

    await page.locator("#candidate-case-list").evaluate((list) => {
      list.innerHTML = Array.from(
        { length: 8 },
        (_, index) => `<li>Candidate case ${index + 1}</li>`,
      ).join("");
    });
    const overflowing = await layout(page);
    assert.ok(overflowing.drawerScrollHeight > overflowing.drawerClientHeight);
    await page.locator("#results-toggle").scrollIntoViewIfNeeded();
    const scrolled = await layout(page);
    assert.ok(scrolled.resultsToggleTop >= scrolled.drawerTop - 1);
    assert.ok(scrolled.resultsToggleBottom <= scrolled.drawerBottom + 1);
    await page.locator("#candidate-case-list").evaluate((list) => {
      list.replaceChildren();
      document.querySelector("#test-drawer").scrollTop = 0;
    });
    await page.focus("#test-drawer-resizer");
    await page.keyboard.press("End");
    const largest = await layout(page);
    assert.equal(largest.drawer, largest.max);
    const shared = largest.editor + largest.drawer;
    assert.ok(
      largest.drawer >= shared - 97,
      `drawer leaves more than 6rem of the ${shared}px shared room unused`,
    );
    assert.ok(largest.editor >= 95, `editor is only ${largest.editor}px tall`);

    await page.locator("#candidate-case > summary").click();
    await page.focus("#test-drawer-resizer");
    await page.keyboard.press("Enter");
    const reset = await layout(page);
    assert.equal(reset.drawer, initial.drawer);
    assert.equal(reset.stored, null);
  } finally {
    await page.close();
  }
});

test("automatic drawer sizing keeps a usable editor", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage({
    viewport: { width: 1280, height: 600 },
  });
  try {
    await openInterview(page);
    await page.locator("#candidate-case > summary").click();
    await page.evaluate(() => {
      document.querySelector("#candidate-case-list").innerHTML = Array.from(
        { length: 8 },
        (_, index) => `<li>Candidate case ${index + 1}</li>`,
      ).join("");
      document.querySelector("#results-body").innerHTML =
        `<pre>${"compiler output\n".repeat(80)}</pre>`;
    });
    const crowded = await layout(page);
    assert.equal(crowded.editorMin, 96);
    assert.ok(crowded.editor >= crowded.editorMin - 1);
  } finally {
    await page.close();
  }
});

test("disabled execution hides the test drawer divider", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.goto(`${base}/interview.html?editorExecution=0`, {
      waitUntil: "domcontentloaded",
    });
    // Waited for, not read once: init() hides both only after the module's
    // top-level await on the problem fetch, which DOMContentLoaded does not
    // wait for, so a single read races it on a loaded runner.
    await page.locator("#test-drawer-resizer").waitFor({ state: "hidden" });
    await page.locator("#test-drawer").waitFor({ state: "hidden" });
  } finally {
    await page.close();
  }
});
