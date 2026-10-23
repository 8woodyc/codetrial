import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import {
  launchChromium,
  startStaticServer,
  DEFAULT_RUNTIME_CONFIG,
  closeSetupCheck,
} from "./source.js";

let browser, server, base;
before(async () => {
  browser = await launchChromium();
  if (browser)
    ({ server, base } = await startStaticServer({
      runtimeConfig: DEFAULT_RUNTIME_CONFIG,
    }));
});
after(async () => {
  await browser?.close();
  await new Promise((resolve) => (server ? server.close(resolve) : resolve()));
});
async function open(t, width = 1280, height = 720) {
  const page = await browser.newPage({ viewport: { width, height } });
  t.after(() => page.close());
  await page.goto(`${base}/interview.html?problem=chargeback-pair-match`);
  await openControls(page);
  return page;
}

async function openControls(page) {
  await page.waitForFunction(
    () => document.querySelector("#candidate-case-input").placeholder !== "",
  );
  await closeSetupCheck(page);
  await page.locator(".jim-volume-controls summary").click();
}

test("volume controls preserve positive level across reload and expose session mute to keyboard users", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await open(t);
  const range = page.getByRole("slider", { name: /Jim volume/ });
  await range.evaluate((input) => {
    input.value = "27";
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await page.waitForFunction(
    () => document.querySelector("#jim-volume-value").textContent === "27%",
  );
  await range.focus();
  await range.press("Home");
  await page.waitForFunction(
    () => !document.querySelector("#jim-muted-indicator").hidden,
  );
  assert.equal(
    await page.locator("#jim-mute").getAttribute("aria-pressed"),
    "true",
  );
  assert.equal(
    await page.evaluate(() => localStorage.getItem("codetrial:jimVolume")),
    "27",
  );
  await range.press("ArrowRight");
  await page.waitForFunction(
    () => document.querySelector("#jim-muted-indicator").hidden,
  );
  assert.equal(await range.inputValue(), "27");
  assert.equal(
    await page.evaluate(() => localStorage.getItem("codetrial:jimVolume")),
    "27",
  );
  await range.press("Home");
  await page.reload();
  await openControls(page);
  assert.equal(await range.inputValue(), "27");
  assert.equal(await page.locator("#jim-muted-indicator").isVisible(), false);
  await page.locator("#jim-mute").click();
  await page.locator("#jim-volume-reset").click();
  await page.waitForFunction(
    () => document.querySelector("#jim-volume-value").textContent === "100%",
  );
  assert.equal(
    await page.locator("#jim-mute").getAttribute("aria-pressed"),
    "false",
  );
});

test("expanded audio controls, mute and playback recovery leave ending actions clear on a phone", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await open(t, 390, 480);
  await page.locator("#jim-mute").click();
  await page.evaluate(() => {
    document.querySelector("#ending-overlay").hidden = false;
    document.querySelector("#ending-withdraw-consent").hidden = false;
    document.querySelector("#audio-playback-notice").hidden = false;
    document.querySelector("#captions-text").textContent =
      "Thanks for walking through the solution. ".repeat(25);
  });
  await page.waitForFunction(
    () =>
      parseFloat(
        getComputedStyle(document.querySelector("#ending-overlay"))
          .paddingBottom,
      ) >=
      document.querySelector("#jim-stage").getBoundingClientRect().height + 47,
  );
  await page.locator("#ending-overlay").evaluate((overlay) => {
    overlay.scrollTop = overlay.scrollHeight;
  });
  const overlap = await page.evaluate(() => {
    const stage = document.querySelector("#jim-stage").getBoundingClientRect();
    return [...document.querySelectorAll("#ending-overlay button")]
      .filter((button) => button.offsetParent)
      .filter((button) => {
        const box = button.getBoundingClientRect();
        return box.bottom > stage.top && box.top < stage.bottom;
      })
      .map((button) => button.id);
  });
  assert.deepEqual(overlap, []);
  assert.equal(await page.locator("#jim-mute").isVisible(), true);
  const actions = await page.evaluate(() => {
    const stage = document.querySelector("#jim-stage").getBoundingClientRect();
    return [...document.querySelectorAll("#ending-overlay button")]
      .filter((button) => button.offsetParent)
      .map((button) => {
        const box = button.getBoundingClientRect();
        return {
          id: button.id,
          reachable:
            box.top >= 0 &&
            box.bottom <= stage.top &&
            box.bottom <= innerHeight,
        };
      });
  });
  assert.ok(actions.length > 0);
  assert.ok(
    actions.every((action) => action.reachable),
    JSON.stringify(actions),
  );
  await page.locator("#resume-audio").focus();
  assert.equal(
    await page.locator("#resume-audio").evaluate((button) => {
      const box = button.getBoundingClientRect();
      return button.contains(
        document.elementFromPoint(
          box.x + box.width / 2,
          box.y + box.height / 2,
        ),
      );
    }),
    true,
  );
});
