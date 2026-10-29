// Run with: node --test tests/browser/test-drawer-headers.test.js

import { after, before, test } from "node:test";
import assert from "node:assert/strict";

import {
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

async function waitForTransformTransition(marker, action) {
  await action();

  // Reading the computed style flushes pending style, which starts the
  // transition; waiting on the animations themselves keeps this independent
  // of how often a loaded runner delivers frames.
  await marker.evaluate(async (el) => {
    getComputedStyle(el).transform;
    const finished = Promise.all(
      el.getAnimations().map((animation) => animation.finished),
    );
    let timer = null;
    const expired = new Promise((_, reject) => {
      timer = setTimeout(
        () => reject(new Error("Chevron transition did not finish")),
        5000,
      );
    });
    try {
      await Promise.race([finished, expired]);
    } finally {
      clearTimeout(timer);
    }
    await new Promise(requestAnimationFrame);
  });
}

test("drawer headers share typography and toggle their chevrons", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");

  const page = await browser.newPage();
  try {
    await page.goto(`${base}/interview.html`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForFunction(
      () => document.querySelector("#candidate-case-input").placeholder !== "",
    );

    await page.evaluate(() => {
      document.querySelector("#audio-check").hidden = true;
    });
    const headers = [
      {
        selector: "#candidate-case > summary",
        markerSelector: "#candidate-case > summary .drawer-chevron",
        open: async () => {
          await page.locator("#candidate-case > summary").click();
        },
        close: async () => {
          await page.locator("#candidate-case > summary").click();
        },
      },
      {
        selector: "#results-toggle",
        markerSelector: "#results-chevron",
        open: async () => {
          await page.locator("#results-toggle").click();
        },
        close: async () => {
          await page.locator("#results-toggle").click();
        },
      },
    ];
    const resultsToggle = page.locator("#results-toggle");

    if ((await resultsToggle.getAttribute("aria-expanded")) === "true") {
      await resultsToggle.click();
    }

    await page.waitForFunction(() => {
      const button = document.querySelector("#results-toggle");
      const body = document.querySelector("#results-body");
      return button.getAttribute("aria-expanded") === "false" && body.hidden;
    });

    const styles = await Promise.all(
      headers.map(async ({ selector }) => {
        const header = page.locator(selector);

        const headerStyle = await header.evaluate((el) => {
          const style = getComputedStyle(el);
          return {
            fontFamily: style.fontFamily,
            fontSize: style.fontSize,
            fontWeight: style.fontWeight,
            lineHeight: style.lineHeight,
            cursor: style.cursor,
          };
        });

        return headerStyle;
      }),
    );

    assert.deepEqual(
      styles[0],
      styles[1],
      "both headers should share typography",
    );

    assert.equal(
      styles[0].cursor,
      "pointer",
      "header cursor should be pointer",
    );
    assert.equal(
      styles[0].fontWeight,
      "700",
      "header font weight should be 700",
    );
    assert.equal(styles[0].fontSize, "14px", "header font size should be 14px");

    const transforms = [];

    for (const header of headers) {
      const marker = page.locator(header.markerSelector);
      const toggle = page.locator(header.selector);

      assert.equal(await marker.count(), 1);

      const closedTransform = await marker.evaluate(
        (el) => getComputedStyle(el).transform,
      );

      await waitForTransformTransition(marker, header.open);

      const openTransform = await marker.evaluate(
        (el) => getComputedStyle(el).transform,
      );

      if (header.selector === "#results-toggle") {
        await page.waitForFunction(() => {
          const button = document.querySelector("#results-toggle");
          const body = document.querySelector("#results-body");
          return (
            button.getAttribute("aria-expanded") === "true" && !body.hidden
          );
        });
      } else {
        assert.equal(
          await page.locator("#candidate-case").evaluate((el) => el.open),
          true,
          "candidate case should be open",
        );
      }

      assert.notEqual(
        openTransform,
        closedTransform,
        `${header.selector} chevron should change when opened`,
      );

      transforms.push({ closed: closedTransform, open: openTransform });

      await waitForTransformTransition(marker, header.close);

      const restoredTransform = await marker.evaluate(
        (el) => getComputedStyle(el).transform,
      );

      if (header.selector === "#results-toggle") {
        assert.equal(
          await toggle.getAttribute("aria-expanded"),
          "false",
          "results toggle should announce that the panel is closed",
        );
        assert.equal(
          await page.locator("#results-body").evaluate((el) => el.hidden),
          true,
          "results body should be hidden when closed",
        );
      } else {
        assert.equal(
          await page.locator("#candidate-case").evaluate((el) => el.open),
          false,
          "candidate case should be closed",
        );
      }

      assert.equal(
        restoredTransform,
        closedTransform,
        `${header.selector} chevron should return when closed`,
      );
    }

    assert.equal(
      transforms[0].closed,
      transforms[1].closed,
      "both chevrons should share the same closed transform",
    );
    assert.equal(
      transforms[0].open,
      transforms[1].open,
      "both chevrons should share the same open transform",
    );
  } finally {
    await page.close();
  }
});
