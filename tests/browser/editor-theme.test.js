// Run with: node --test tests/browser/editor-theme.test.js
//
// The editor theme toggle swaps CSS custom properties scoped to .editor-stack
// rather than touching the page-wide dark scheme. What can break is any of
// the five editor surfaces (background, text, gutter, caret, selection)
// missing the swap, the choice not surviving a reload, a switch disturbing
// the candidate's code or cursor, or the no-choice default depending on
// interview.js having run instead of on the stylesheet alone.

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

/// Out from behind the media preflight, which covers the toolbar until a
/// device check clears it.
async function openEditor(page) {
  await page.waitForFunction(
    () => document.querySelector("#candidate-case-input").placeholder !== "",
  );
  await closeSetupCheck(page);
}

/// def and return are Python keywords (see highlight.test.js), so this gives
/// the highlight overlay a real .tok-keyword span to read a computed color
/// from, rather than trusting the --tok-* custom property alone: the class
/// could stop reading it without this failing.
async function fillKeyword(page) {
  await page.evaluate(() => {
    const editor = document.querySelector("#editor");
    editor.value = "def solve():\n    return 42\n";
    editor.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await page.locator("#editor-highlight .tok-keyword").first().waitFor();
}

/// Shared between the explicit-choice and the system-theme-only paths, so the
/// two light palettes (.editor-stack[data-theme="light"] and the
/// prefers-color-scheme media query) cannot drift apart with both still
/// green: a token changed in one block and not the other fails here either
/// way, rather than only when a candidate happens to click the toggle.
function assertLightTheme(metrics, label) {
  assert.equal(metrics.stackBg, "rgb(255, 255, 255)", label);
  assert.equal(metrics.textColor, "rgb(31, 31, 31)", label);
  assert.equal(metrics.gutterBg, "rgb(243, 243, 243)", label);
  assert.equal(metrics.gutterInk, "rgb(89, 89, 89)", label);
  assert.equal(metrics.caretColor, "rgb(0, 0, 0)", label);
  assert.equal(metrics.keywordColor, "rgb(0, 0, 255)", label);
  assert.equal(metrics.selectionVar, "#add6ff", label);
  assert.equal(metrics.selectionInkVar, "#1f1f1f", label);
}

const themeMetrics = (page) =>
  page.evaluate(() => {
    const stack = document.querySelector(".editor-stack");
    const highlight = document.querySelector("#editor-highlight");
    const lines = document.querySelector("#editor-lines");
    const editor = document.querySelector("#editor");
    const toggle = document.querySelector("#editor-theme-toggle");
    const keyword = document.querySelector("#editor-highlight .tok-keyword");
    const stackStyle = getComputedStyle(stack);
    return {
      // null with no explicit choice: the no-choice default is carried by
      // the stylesheet's media query, not by interview.js setting this.
      dataTheme: stack.dataset.theme ?? null,
      stackBg: getComputedStyle(stack).backgroundColor,
      textColor: getComputedStyle(highlight).color,
      gutterBg: getComputedStyle(lines).backgroundColor,
      gutterInk: getComputedStyle(lines).color,
      caretColor: getComputedStyle(editor).caretColor,
      keywordColor: keyword ? getComputedStyle(keyword).color : null,
      selectionVar: stackStyle.getPropertyValue("--code-selection").trim(),
      selectionInkVar: stackStyle
        .getPropertyValue("--code-selection-ink")
        .trim(),
      toggleLabel: toggle.textContent.trim(),
      togglePressed: toggle.getAttribute("aria-pressed"),
      stored: localStorage.getItem("codetrial:editorTheme"),
    };
  });

/// The page's own scheme and background, as the browser resolved them.
const bodyPalette = (page) =>
  page.evaluate(() => {
    const style = getComputedStyle(document.body);
    return { scheme: style.colorScheme, background: style.backgroundColor };
  });

test("the light practice entrance does not change the live interview palette", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.goto(`${base}/index.html`, { waitUntil: "domcontentloaded" });
    assert.deepEqual(await bodyPalette(page), {
      scheme: "light",
      background: "rgb(246, 246, 249)",
    });

    await page.goto(`${base}/interview.html`, {
      waitUntil: "domcontentloaded",
    });
    await openEditor(page);
    assert.deepEqual(await bodyPalette(page), {
      scheme: "dark",
      background: "rgb(17, 17, 15)",
    });
  } finally {
    await page.close();
  }
});

test("the theme toggle switches every editor surface and is remembered", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto(`${base}/interview.html`, {
      waitUntil: "domcontentloaded",
    });
    await openEditor(page);
    await fillKeyword(page);

    const dark = await themeMetrics(page);
    assert.equal(dark.stackBg, "rgb(30, 30, 30)");
    assert.equal(dark.textColor, "rgb(212, 212, 212)");
    assert.equal(dark.gutterBg, "rgb(24, 24, 22)");
    assert.equal(dark.gutterInk, "rgb(125, 119, 106)");
    assert.equal(dark.caretColor, "rgb(244, 244, 240)");
    assert.equal(dark.keywordColor, "rgb(86, 156, 214)");
    assert.equal(dark.selectionVar, "#264f78");
    assert.equal(dark.toggleLabel, "Light editor theme");
    assert.equal(dark.togglePressed, "false");
    assert.equal(dark.stored, null, "no click yet, nothing stored");

    await page.click("#editor-theme-toggle");
    const light = await themeMetrics(page);
    assert.equal(light.dataTheme, "light", "a click is an explicit choice");
    assertLightTheme(light, "explicit choice");
    assert.equal(light.toggleLabel, "Light editor theme", "the label is fixed");
    assert.equal(light.togglePressed, "true");
    assert.equal(light.stored, "light");

    await page.reload({ waitUntil: "domcontentloaded" });
    await openEditor(page);
    const returning = await themeMetrics(page);
    assert.equal(returning.dataTheme, "light", "the choice is remembered");
    assert.equal(returning.stackBg, "rgb(255, 255, 255)");
  } finally {
    await page.close();
  }
});

test("with no stored choice, the editor follows the system theme from the stylesheet, not from interview.js", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto(`${base}/interview.html`, {
      waitUntil: "domcontentloaded",
    });
    await openEditor(page);
    await fillKeyword(page);
    const metrics = await themeMetrics(page);
    assert.equal(
      metrics.dataTheme,
      null,
      "no explicit choice, so nothing is set on the element",
    );
    assertLightTheme(metrics, "system theme, no stored choice");
    assert.equal(metrics.stored, null);

    // The click handler used to compare dataset.theme to "light" directly,
    // which is unset in exactly this state; the first click recomputed
    // "light" again and the editor never moved.
    await page.click("#editor-theme-toggle");
    const clicked = await themeMetrics(page);
    assert.equal(clicked.dataTheme, "dark", "the first click reaches dark");
    assert.equal(clicked.stackBg, "rgb(30, 30, 30)");
  } finally {
    await page.close();
  }
});

test("with no stored choice, a system theme change reaches the editor without a reload", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto(`${base}/interview.html`, {
      waitUntil: "domcontentloaded",
    });
    await openEditor(page);
    const dark = await themeMetrics(page);
    assert.equal(dark.stackBg, "rgb(30, 30, 30)");

    // No reload, no click: a CSS media query, unlike the one-time matchMedia
    // read applyEditorTheme used before, re-evaluates live. This is the CSS
    // side only -- emulateMedia does not fire matchMedia's own "change"
    // event in Chromium, confirmed separately, so aria-pressed keeping pace
    // with a system change is covered by the stubbed test below instead.
    await page.emulateMedia({ colorScheme: "light" });
    const metrics = await themeMetrics(page);
    assert.equal(metrics.stackBg, "rgb(255, 255, 255)");
    assert.equal(
      metrics.dataTheme,
      null,
      "still no explicit choice; the stylesheet alone followed the switch",
    );
  } finally {
    await page.close();
  }
});

test("a prefers-color-scheme change event re-applies the theme", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    // emulateMedia above proves the CSS side follows a system change live,
    // but Chromium under Playwright never fires matchMedia's own "change"
    // event from it, only the style recalculation -- confirmed separately
    // against about:blank. This stubs the one query interview.js reads
    // before it loads, so the test can fire that event directly and prove
    // the listener itself re-applies the theme, rather than needing
    // Playwright to simulate a real OS-level change, which it cannot.
    await page.addInitScript(() => {
      const real = window.matchMedia.bind(window);
      const stub = {
        matches: false,
        media: "(prefers-color-scheme: light)",
        listeners: [],
        addEventListener(type, fn) {
          if (type === "change") this.listeners.push(fn);
        },
        removeEventListener() {},
      };
      window.__lightQueryStub = stub;
      window.matchMedia = (query) =>
        query === stub.media ? stub : real(query);
    });
    await page.goto(`${base}/interview.html`, {
      waitUntil: "domcontentloaded",
    });
    await openEditor(page);
    assert.equal((await themeMetrics(page)).togglePressed, "false");

    await page.evaluate(() => {
      window.__lightQueryStub.matches = true;
      for (const fn of window.__lightQueryStub.listeners) {
        fn({ matches: true });
      }
    });
    const metrics = await themeMetrics(page);
    assert.equal(
      metrics.togglePressed,
      "true",
      "the change listener re-ran applyEditorTheme",
    );
    assert.equal(
      metrics.dataTheme,
      null,
      "still no explicit choice, just a re-applied default",
    );
  } finally {
    await page.close();
  }
});

test("an explicit dark choice overrides a light system theme", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.addInitScript(() =>
      localStorage.setItem("codetrial:editorTheme", "dark"),
    );
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto(`${base}/interview.html`, {
      waitUntil: "domcontentloaded",
    });
    await openEditor(page);
    const metrics = await themeMetrics(page);
    assert.equal(metrics.dataTheme, "dark");
    assert.equal(metrics.stackBg, "rgb(30, 30, 30)");
  } finally {
    await page.close();
  }
});

test("switching themes changes neither the code nor the cursor position", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.goto(`${base}/interview.html`, {
      waitUntil: "domcontentloaded",
    });
    await openEditor(page);

    await page.evaluate(() => {
      const editor = document.querySelector("#editor");
      editor.value = "def solve():\n    return 42\n";
      editor.selectionStart = 4;
      editor.selectionEnd = 9;
      editor.dispatchEvent(new Event("input", { bubbles: true }));
    });

    await page.click("#editor-theme-toggle");

    const after = await page.evaluate(() => {
      const editor = document.querySelector("#editor");
      return {
        value: editor.value,
        selectionStart: editor.selectionStart,
        selectionEnd: editor.selectionEnd,
      };
    });
    assert.equal(after.value, "def solve():\n    return 42\n");
    assert.equal(after.selectionStart, 4);
    assert.equal(after.selectionEnd, 9);
  } finally {
    await page.close();
  }
});
