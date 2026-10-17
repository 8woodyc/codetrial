// Run with: node --test tests/browser/interview-layout.test.js
//
// Where the interview page puts its controls. Each case is a placement a
// redesign could quietly undo while every behavior test stays green: Start
// scrolled out of the setup check, the setup check no longer modal, End
// interview pushed out of a short sidebar, Run tests wrapped onto a second
// toolbar row, the dock a screen below the problem on a phone.

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

async function openInterview(width, height) {
  const page = await browser.newPage({ viewport: { width, height } });
  await page.goto(`${base}/interview.html?problem=chargeback-pair-match`, {
    waitUntil: "domcontentloaded",
  });
  await page.waitForFunction(
    () => document.querySelector("#candidate-case-input").placeholder !== "",
  );
  return page;
}

/// What holdWorkspaceBehindPreflight in web/interview.js makes inert.
const WORKSPACE = [
  "#interview-sidebar",
  "#sidebar-resizer",
  ".editor-panel",
  "#board-panel",
];

const workspaceInert = (page) =>
  page.evaluate(
    (selectors) =>
      selectors.map((selector) => document.querySelector(selector).inert),
    WORKSPACE,
  );

test("Start interview is on screen in the setup check without scrolling", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await openInterview(1280, 720);
  try {
    const layout = await page.evaluate(() => {
      const start = document
        .querySelector("#audio-check-join")
        .getBoundingClientRect();
      const back = document
        .querySelector("#audio-check-leave")
        .getBoundingClientRect();
      return {
        start: { top: start.top, bottom: start.bottom },
        back: { top: back.top, bottom: back.bottom },
        overlayScrolled: document.querySelector("#audio-check").scrollTop,
        viewport: innerHeight,
      };
    });
    assert.equal(layout.overlayScrolled, 0);
    for (const [name, box] of Object.entries({
      start: layout.start,
      back: layout.back,
    })) {
      assert.ok(
        box.top >= 0 && box.bottom <= layout.viewport,
        `${name} sits at ${box.top}-${box.bottom}, outside a ${layout.viewport}px window`,
      );
    }
  } finally {
    await page.close();
  }
});

test("the setup check is a modal dialog over an inert workspace", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await openInterview(1280, 800);
  try {
    const open = await page.evaluate(() => {
      const dialog = document.querySelector("#audio-check");
      return {
        role: dialog.getAttribute("role"),
        modal: dialog.getAttribute("aria-modal"),
        name: document.getElementById(dialog.getAttribute("aria-labelledby"))
          ?.textContent,
        focused: document.activeElement.id,
      };
    });
    assert.equal(open.role, "dialog");
    assert.equal(open.modal, "true");
    assert.equal(open.name, "Check your setup");
    assert.equal(open.focused, "audio-check-title");
    assert.deepEqual(await workspaceInert(page), [true, true, true, true]);

    // Tab from the title stays in the dialog: the workspace behind it offers
    // nothing to land on.
    await page.keyboard.press("Tab");
    assert.equal(
      await page.evaluate(() =>
        document.querySelector("#audio-check").contains(document.activeElement),
      ),
      true,
    );

    await closeSetupCheck(page);
    assert.deepEqual(await workspaceInert(page), [false, false, false, false]);
  } finally {
    await page.close();
  }
});

test("the dock fits in the sidebar with the Meet notes open on a short screen", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await openInterview(1280, 720);
  try {
    await closeSetupCheck(page);
    await page.locator("#meet-mode-summary").click();
    await page.waitForFunction(() => document.querySelector("#meet-mode").open);
    const layout = await page.evaluate(() => {
      const box = (selector) => {
        const { top, bottom } = document
          .querySelector(selector)
          .getBoundingClientRect();
        return { top, bottom };
      };
      return {
        dock: box(".session-dock"),
        end: box("#end"),
        turn: box("#yield-turn"),
        sidebar: box("#interview-sidebar"),
      };
    });
    // The whole dock, Meet notes included: the sidebar clips what overflows
    // it, so a dock taller than its room loses its bottom (the Meet output
    // picker) with no way to scroll to it.
    for (const name of ["dock", "end", "turn"])
      assert.ok(
        layout[name].top >= layout.sidebar.top &&
          layout[name].bottom <= layout.sidebar.bottom,
        `the ${name} is cut off by the sidebar with the Meet notes open`,
      );
    // Apart from the action used most, below it rather than beside it.
    assert.ok(layout.end.top >= layout.turn.bottom);
  } finally {
    await page.close();
  }
});

test("Run tests shares one toolbar row with the languages on a laptop", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await openInterview(1280, 800);
  try {
    await closeSetupCheck(page);
    const rows = await page.evaluate(() => {
      const middle = (selector) => {
        const box = document.querySelector(selector).getBoundingClientRect();
        return box.top + box.height / 2;
      };
      return {
        language: middle('.language-button[data-language="python"]'),
        run: middle("#run-tests"),
        theme: middle("#editor-theme-toggle"),
      };
    });
    assert.ok(
      Math.abs(rows.run - rows.language) < 4,
      `Run tests centres at ${rows.run}, the languages at ${rows.language}`,
    );
    assert.ok(Math.abs(rows.theme - rows.language) < 4);
  } finally {
    await page.close();
  }
});

test("on a phone the turn button is on the first screen, under the problem", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await openInterview(390, 844);
  try {
    await closeSetupCheck(page);
    const layout = await page.evaluate(() => ({
      turn: document.querySelector("#yield-turn").getBoundingClientRect()
        .bottom,
      problem: document.querySelector("#problem-panel").getBoundingClientRect()
        .top,
      viewport: innerHeight,
    }));
    assert.ok(
      layout.turn <= layout.viewport,
      `Your turn is done ends at ${layout.turn}, past a ${layout.viewport}px screen`,
    );
    assert.ok(layout.turn > layout.problem);
  } finally {
    await page.close();
  }
});
