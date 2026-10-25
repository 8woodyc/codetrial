import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_RUNTIME_CONFIG,
  launchChromium,
  FAKE_SPEECH_CAPTURE,
  startStaticServer,
} from "./source.js";
import { LANGUAGE_KEY } from "../../web/lib.js";

// C is offered only once the judge proves the problem function-style, so a
// compiled runner has to be on for the requested tab to exist at all.
const CE_RUNTIME_CONFIG = DEFAULT_RUNTIME_CONFIG.replace(
  "ENABLED = false",
  "ENABLED = true",
).replace('BASE_URL = ""', 'BASE_URL = "https://godbolt.org"');
let browser = null;
let server = null;
let base = "";

before(async () => {
  browser = await launchChromium({
    args: [
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream",
      FAKE_SPEECH_CAPTURE,
      "--autoplay-policy=no-user-gesture-required",
    ],
  });
  if (!browser) return;
  ({ server, base } = await startStaticServer({
    runtimeConfig: CE_RUNTIME_CONFIG,
  }));
});

after(async () => {
  await browser?.close();
  await new Promise((closed) => (server ? server.close(closed) : closed()));
});

/// Joins through the preflight and answers the token request's body. The
/// token is refused unless `granted`, which answers it with an empty grant:
/// enough for the page to count the interview as started, not to join a room.
async function joinAndCaptureToken(page, { granted = false } = {}) {
  await page.route("**/api/token", (route) =>
    route.fulfill(
      granted
        ? { status: 200, contentType: "application/json", body: "{}" }
        : {
            status: 503,
            contentType: "application/json",
            body: JSON.stringify({
              error: "Token request captured by the test.",
            }),
          },
    ),
  );
  await page.locator("#audio-heard").click();
  await page.locator("#camera-skip").click();
  await page.waitForFunction(
    () => !document.querySelector("#audio-check-join").disabled,
  );
  const sent = page.waitForRequest((request) =>
    request.url().endsWith("/api/token"),
  );
  await page.locator("#audio-check-join").click();
  return sent;
}

/// Past `init()`, which runs after the problem loads and so after
/// `domcontentloaded`: until then the menu shows the markup's Python, and a pick
/// made before it is overwritten. The placeholder is written once the opening
/// language is set and the menu has its handler.
const interviewReady = (page) =>
  page.waitForFunction(
    () => document.querySelector("#candidate-case-input").placeholder !== "",
  );

test("the token names a requested C whose judge loads after Start", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  let releaseJudge = () => {};
  try {
    const held = new Promise((resolve) => {
      releaseJudge = resolve;
    });
    await page.route("**/judges/*.json", async (route) => {
      await held;
      await route.continue();
    });
    await page.goto(
      `${base}/interview.html?problem=chargeback-pair-match&language=c`,
      { waitUntil: "domcontentloaded" },
    );
    let tokenSent = false;
    page.on("request", (request) => {
      if (request.url().endsWith("/api/token")) tokenSent = true;
    });
    const request = joinAndCaptureToken(page);
    await page.waitForFunction(
      () => document.querySelector("#audio-check").hidden,
    );
    assert.equal(tokenSent, false, "the token waits for the judge");
    releaseJudge();
    assert.equal((await request).postDataJSON().language, "c");
    // The token was refused, so no interview started in C.
    await page.waitForFunction(
      () => !document.querySelector("#audio-check-join").disabled,
    );
    assert.equal(
      await page.evaluate((key) => localStorage.getItem(key), LANGUAGE_KEY),
      null,
    );
  } finally {
    releaseJudge();
    await page.close();
  }
});

/// Holds every judge request until the returned function is called, so a
/// test can act while the page is still waiting for one.
async function holdJudges(page) {
  let release = () => {};
  const held = new Promise((resolve) => {
    release = resolve;
  });
  await page.route("**/judges/*.json", async (route) => {
    await held;
    await route.continue();
  });
  return () => release();
}

for (const [query, expected] of [
  ["", "python"],
  ["&language=java", "java"],
]) {
  test(`a ${expected} start neither waits for nor depends on a pending judge`, async (t) => {
    if (!browser) return t.skip("playwright chromium unavailable");
    const page = await browser.newPage();
    const releaseJudge = await holdJudges(page);
    try {
      await page.goto(
        `${base}/interview.html?problem=chargeback-pair-match${query}`,
        { waitUntil: "domcontentloaded" },
      );
      // The placeholder waits for the judge; the starter buffer does not.
      await page.waitForFunction(
        () => document.querySelector("#editor").value !== "",
      );
      // Only C needs the judge, so the tab is already the one asked for.
      assert.equal(
        await page.locator("#language-select").inputValue(),
        expected,
      );
      // Well inside the wait a requested C gets.
      page.setDefaultTimeout(2_000);
      const body = (await joinAndCaptureToken(page)).postDataJSON();
      assert.equal(body.language, expected);
    } finally {
      releaseJudge();
      await page.close();
    }
  });
}

test("a judge slower than the wait does not hold the start", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  let releaseJudge = () => {};
  try {
    const held = new Promise((resolve) => {
      releaseJudge = resolve;
    });
    await page.route("**/judges/*.json", async (route) => {
      await held;
      await route.continue();
    });
    await page.goto(
      `${base}/interview.html?problem=chargeback-pair-match&language=c`,
      { waitUntil: "domcontentloaded" },
    );
    // C stays unconfirmed, so the interview starts in the fallback.
    const body = (await joinAndCaptureToken(page)).postDataJSON();
    assert.equal(body.language, "python");
    releaseJudge();
    await page.waitForFunction(() =>
      [
        ...document.querySelectorAll('#language-select option[value="c"]'),
      ].every((option) => !option.disabled),
    );
    assert.equal(await page.locator("#language-select").inputValue(), "python");
  } finally {
    releaseJudge();
    await page.close();
  }
});

test("a started interview remembers its language", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.goto(
      `${base}/interview.html?problem=chargeback-pair-match&language=java`,
      { waitUntil: "domcontentloaded" },
    );
    await interviewReady(page);
    const body = (
      await joinAndCaptureToken(page, { granted: true })
    ).postDataJSON();
    assert.equal(body.language, "java");
    await page.waitForFunction(
      (key) => localStorage.getItem(key) === "java",
      LANGUAGE_KEY,
    );
  } finally {
    await page.close();
  }
});

test("a whiteboard token carries no language", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await browser.newPage();
  try {
    await page.goto(
      `${base}/interview.html?problem=chargeback-pair-match&mode=whiteboard`,
      { waitUntil: "domcontentloaded" },
    );
    const body = (await joinAndCaptureToken(page)).postDataJSON();
    assert.equal(Object.hasOwn(body, "language"), false);
  } finally {
    await page.close();
  }
});
