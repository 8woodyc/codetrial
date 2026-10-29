import assert from "node:assert/strict";
import { createServer } from "node:http";
import { after, before, test } from "node:test";
import { STRICT_ASSIGNMENT_KEY } from "../../web/selection.js";
import { grant, openInterview, strictFailure } from "./interview-fixture.js";
import {
  DEFAULT_RUNTIME_CONFIG,
  launchChromium,
  read,
  startStaticServer,
} from "./source.js";
import { until } from "./wait.js";

let browser;
let server;
let base;
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

function interview(
  t,
  query,
  handler,
  context = browser,
  session = { signedIn: true, loginRequired: true },
) {
  return openInterview({ browser, base }, t, query, handler, context, session);
}

// A token request that never leaves the page would otherwise hold the whole
// file until its timeout and starve every later case.
function within(promise, label, ms = 10000) {
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(
      () => reject(new Error(`${label} did not arrive within ${ms}ms`)),
      ms,
    );
  });
  return Promise.race([promise, deadline]).finally(() => clearTimeout(timer));
}

// Reach the point where Start is enabled without pressing it.
async function readyToStart(page) {
  await page.locator("#audio-heard").click();
  await page.locator("#camera-skip").click();
  await page.waitForFunction(
    () => !document.querySelector("#audio-check-join").disabled,
  );
}

// Two clicks in one task, before any response can re-render the button.
async function doubleStart(page) {
  await page.locator("#audio-check-join").evaluate((button) => {
    button.click();
    button.click();
  });
}

const RECORDING_FIXTURE_VERSION = "fixture-consent-v1";
const RECORDING_FIXTURE_REFUSAL =
  "Fixture: a verified recording identity is required.";

async function pendingResponses(t) {
  const calls = [];
  const errors = [];
  const routeErrors = [];
  let firstSuperseded = false;
  let responseOf;
  let routed = 0;
  const server = createServer((request, response) => {
    response.setHeader(
      "Access-Control-Allow-Origin",
      request.headers.origin || base,
    );
    response.setHeader("Access-Control-Allow-Credentials", "true");
    response.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
    response.setHeader("Access-Control-Allow-Headers", "Content-Type");
    // Routing rewrites the destination to another loopback port. Accept a
    // preflight if the browser requires one, without counting it as a Start.
    if (request.method === "OPTIONS") {
      response.writeHead(204).end();
      return;
    }
    const index = calls.length;
    let release, settle, completed;
    const held = new Promise((resolve) => {
      release = resolve;
    });
    const settled = new Promise((resolve) => {
      settle = resolve;
    });
    const ended = new Promise((resolve) => {
      completed = resolve;
    });
    const call = {
      release,
      settled,
      body: {},
      received: false,
      aborted: false,
    };
    calls.push(call);
    response.once("close", () => {
      call.aborted = !response.writableFinished;
      completed();
    });
    response.once("finish", completed);
    response.on("error", () => {
      call.aborted = true;
      completed();
    });
    void (async () => {
      let reply;
      try {
        let body = "";
        for await (const chunk of request) {
          body += chunk.toString();
          if (body.length > 65536)
            throw new Error("Fixture body exceeds limit");
        }
        if (request.method !== "POST") throw new Error("Fixture expects POST");
        call.body = JSON.parse(body);
        reply = responseOf(index, call.body);
      } catch {
        errors.push(index);
        reply = { status: 500, json: { error: "Invalid fixture request" } };
      }
      call.received = true;
      await held;
      try {
        if (!response.destroyed && !response.writableEnded && !call.aborted) {
          response.setHeader("Content-Type", "application/json");
          response.writeHead(reply.status || 200);
          response.end(JSON.stringify(reply.json));
        }
        await ended;
      } catch {
        errors.push(index);
      } finally {
        settle();
      }
    })();
  });
  server.on("error", () => errors.push(-1));
  t.after(async () => {
    for (const call of calls) call.release();
    const bounded = (promise) =>
      within(promise, "teardown", 5000).then(
        () => true,
        () => false,
      );
    const settled = await bounded(
      Promise.all(calls.map((call) => call.settled)),
    );
    server.closeAllConnections();
    const closed = await bounded(
      new Promise((resolve) => server.close(resolve)),
    );
    if (!settled || !closed) errors.push(-1);
    for (const [index, call] of calls.entries())
      if (call.aborted && !(index === 0 && firstSuperseded)) errors.push(index);
    for (const failed of routeErrors)
      if (!(failed.index === 0 && firstSuperseded)) errors.push(failed.index);
    assert.equal(
      routed,
      calls.length,
      "every token POST must reach its response fixture",
    );
    assert.deepEqual(
      errors,
      [],
      "only the first superseded document request may be canceled",
    );
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const endpoint = `http://127.0.0.1:${server.address().port}/api/token`;
  return {
    calls,
    get routed() {
      return routed;
    },
    async markSuperseded(oldDocument, newDocument) {
      assert.equal(calls.length, 1);
      assert.equal(
        routed,
        1,
        "only the old document's request exists before reload",
      );
      assert.notEqual(
        oldDocument,
        newDocument,
        "reload must create a new document",
      );
      // Chromium can omit requestfailed for a routed request from the old
      // document. The real response socket closing before any write is the
      // cancellation signal; neither the fixture nor the test aborts it.
      await until(
        () => calls[0].aborted,
        "the reload to close the held response socket",
      );
      firstSuperseded = true;
    },
    async waitFor(count) {
      await until(
        () => calls.length >= count && calls[count - 1].received,
        `token request ${count} to be observed`,
      );
      return calls[count - 1];
    },
    async handle(route, factory) {
      if (route.request().method() === "OPTIONS")
        return route.continue({ url: endpoint });
      responseOf = factory;
      const index = routed++;
      try {
        await route.continue({ url: endpoint });
      } catch {
        routeErrors.push({ index });
      }
    },
  };
}

// These routes model the measured server outcomes, not OAuth or Egress. The
// real face-check lifecycle sees an explicitly unavailable detector; the
// camera and room are synthetic, while consent and token fetches run normally.
async function recordedPage(
  t,
  selection,
  identity,
  consentRefused = false,
  options = {},
) {
  if (!browser) {
    t.skip("playwright chromium unavailable");
    return null;
  }
  // Registered before context cleanup, so held handlers settle while their
  // page still exists. Only the explicitly superseded document request may be canceled.
  const pending = options.pendingReload ? await pendingResponses(t) : null;
  const context = await browser.newContext();
  t.after(() => context.close());
  const seen = {
    consents: [],
    tokens: [],
    starts: [],
    recordingRequests: 0,
    events: 0,
    order: [],
    unexpected: [],
  };
  const acceptedConsents = new Set();
  let captured;
  const firstToken = new Promise((resolve) => {
    captured = resolve;
  });
  let release;
  const held = new Promise((resolve) => {
    release = resolve;
  });
  t.after(() => release());
  await context.route("**/runtime-config.js", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: DEFAULT_RUNTIME_CONFIG.replace(
        "CODETRIAL_RECORDING_ENABLED = false",
        "CODETRIAL_RECORDING_ENABLED = true",
      ).replace(
        'CODETRIAL_CONSENT_VERSION = ""',
        `CODETRIAL_CONSENT_VERSION = "${RECORDING_FIXTURE_VERSION}"`,
      ),
    }),
  );
  await context.route("**/face-presence.js", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body:
        read("web/face-presence.js").replace(
          "export async function createFacePresenceDetector(",
          "async function unusedFacePresenceDetector(",
        ) +
        `\nexport async function createFacePresenceDetector() {
      globalThis.recordingFixtureDetectorCalls = (globalThis.recordingFixtureDetectorCalls || 0) + 1;
      return {available: false, close() {}};
    }`,
    }),
  );
  await context.route("**/api/**", (route) => {
    // The guest redirect reaches the lobby, which has its own API traffic.
    if (route.request().frame().url().includes("/interview.html"))
      seen.unexpected.push(new URL(route.request().url()).pathname);
    return route.fulfill({
      status: 501,
      json: { error: "Unexpected fixture API" },
    });
  });
  await context.route("**/api/interviews", (route) => {
    const body = route.request().postDataJSON();
    if (body.consentVersion !== RECORDING_FIXTURE_VERSION)
      seen.unexpected.push("consent version mismatch");
    const id = `recorded-${seen.consents.length + 1}`;
    seen.consents.push(id);
    seen.order.push(`consent:${id}`);
    if (!consentRefused) acceptedConsents.add(id);
    return consentRefused
      ? route.fulfill({
          status: 409,
          json: {
            code: "consent_version_mismatch",
            error: "Fixture consent refused",
          },
        })
      : route.fulfill({ status: 201, json: { interviewId: id } });
  });
  await context.route("**/api/interviews/*/recording", (route) => {
    const id = new URL(route.request().url()).pathname.split("/")[3];
    seen.recordingRequests++;
    if (!acceptedConsents.has(id))
      seen.unexpected.push("recording request without accepted consent");
    const method = route.request().method();
    if (!["POST", "GET"].includes(method))
      seen.unexpected.push("unexpected recording method");
    if (method === "POST") {
      seen.starts.push(id);
      seen.order.push(`recording:${id}`);
    }
    return route.fulfill({ json: { state: "recording" } });
  });
  await context.route("**/api/interviews/*/events", (route) => {
    if (route.request().method() !== "POST")
      seen.unexpected.push("unexpected replay method");
    const id = new URL(route.request().url()).pathname.split("/")[3];
    // A keepalive batch from the old page still belongs to the old consent.
    if (!acceptedConsents.has(id))
      seen.unexpected.push("replay request without accepted consent");
    seen.events++;
    return route.fulfill({ status: 204 });
  });
  const page = await interview(
    t,
    new URLSearchParams({ problem: "chargeback-pair-match", selection }),
    async (route) => {
      const responseOf = (index, body) => {
        seen.tokens.push(body);
        seen.order.push(`token:${body.interviewId}`);
        captured();
        return identity === "self-declared"
          ? {
              status: 403,
              json: {
                code: "recording_requires_verified_identity",
                error: RECORDING_FIXTURE_REFUSAL,
              },
            }
          : {
              json: grant(
                "chargeback-pair-match",
                `recorded-assignment-${index + 1}`,
              ),
            };
      };
      if (pending) return pending.handle(route, responseOf);
      const response = responseOf(
        seen.tokens.length,
        route.request().postDataJSON(),
      );
      await held;
      return route.fulfill(response);
    },
    context,
    {
      signedIn: identity !== "guest",
      identityVerified: identity === "oauth",
      loginRequired: true,
      guestModeAvailable: false,
    },
  );
  return { page, seen, release, firstToken, pending };
}

// A pending-response reload does not depend on identity verification, so the
// matrix runs the typed account and the guest. The lifecycle tests below keep
// OAuth, where reconnect is admitted.
for (const recording of [false, true]) {
  for (const identity of ["self-declared", "guest"]) {
    for (const selection of ["practice", "strict"]) {
      if (identity === "guest" && (recording || selection === "strict"))
        continue;
      test(`pending token reload: ${recording ? "on" : "off"} ${identity} ${selection}`, async (t) => {
        if (!browser) return t.skip("playwright chromium unavailable");
        let page, pending, seen;
        if (recording) {
          ({ page, pending, seen } = await recordedPage(
            t,
            selection,
            identity,
            false,
            { pendingReload: true },
          ));
        } else {
          pending = await pendingResponses(t);
          page = await interview(
            t,
            new URLSearchParams({
              problem: "chargeback-pair-match",
              selection,
            }),
            (route) =>
              pending.handle(route, (index) => ({
                json: {
                  ...grant("chargeback-pair-match", `pending-off-${index + 1}`),
                  guest: identity === "guest",
                },
              })),
            browser,
            {
              signedIn: identity !== "guest",
              identityVerified: identity === "oauth",
              loginRequired: true,
              guestModeAvailable: true,
            },
          );
          let consents = 0;
          await page.route("**/api/interviews", (route) => {
            consents++;
            return route.fulfill({
              status: 500,
              json: { error: "Recording is off" },
            });
          });
          t.after(() => assert.equal(consents, 0));
        }
        const ready = async () => {
          if (recording) return recordedReady(page);
          await readyToStart(page);
        };
        await ready();
        await page.locator("#audio-check-join").click();
        const first = await pending.waitFor(1);
        assert.equal(await page.evaluate(() => window.roomJoins.length), 0);
        const oldDocument = await page.evaluate(() => performance.timeOrigin);
        await page.reload({ waitUntil: "domcontentloaded" });
        await pending.markSuperseded(
          oldDocument,
          await page.evaluate(() => performance.timeOrigin),
        );
        await ready();
        assert.equal(pending.calls.length, 1, "new document waits for Start");
        assert.equal(
          await page.evaluate(
            (key) => sessionStorage.getItem(key),
            STRICT_ASSIGNMENT_KEY,
          ),
          null,
        );
        await doubleStart(page);
        const second = await pending.waitFor(2);
        assert.equal(
          pending.calls.length,
          2,
          "second Start is counted while its response is held",
        );
        assert.equal(pending.routed, 2);
        assert.equal(
          second.body.previousAssignment,
          undefined,
          "the aborted grant never supplied a receipt",
        );
        assert.equal(await page.evaluate(() => window.roomJoins.length), 0);
        if (recording) {
          assert.equal(seen.consents.length, 2);
          assert.equal(seen.starts.length, 0);
          assert.equal(second.body.interviewId, seen.consents[1]);
        } else
          assert.ok(
            pending.calls.every((call) => call.body.interviewId === null),
          );
        second.release();
        await second.settled;
        if (recording && identity === "self-declared")
          await recordedRefused(page, selection);
        else {
          await page.waitForFunction(() => window.roomJoins.length === 1);
          if (recording)
            await page.waitForFunction(
              () =>
                document.querySelector("#recording-state").textContent ===
                "Recording.",
            );
          if (selection === "strict")
            assert.equal(
              await page.evaluate(
                (key) => sessionStorage.getItem(key),
                STRICT_ASSIGNMENT_KEY,
              ),
              recording ? "recorded-assignment-2" : "pending-off-2",
            );
        }
        first.release();
        await first.settled;
        // This window observes real network callbacks, including any timer
        // created before the reload. Keep it on wall time, not a fake clock.
        await new Promise((resolve) => setTimeout(resolve, 1000));
        assert.equal(pending.routed, 2);
        assert.equal(pending.calls.length, 2);
        assert.equal(
          await page.evaluate(() => window.roomJoins.length),
          recording && identity === "self-declared" ? 0 : 1,
        );
        if (selection === "strict")
          assert.equal(
            await page.evaluate(
              (key) => sessionStorage.getItem(key),
              STRICT_ASSIGNMENT_KEY,
            ),
            recording && identity === "self-declared"
              ? null
              : recording
                ? "recorded-assignment-2"
                : "pending-off-2",
            "only the second document's received grant supplies the stored receipt",
          );
        if (recording) {
          assert.deepEqual(
            seen.starts,
            identity === "self-declared" ? [] : ["recorded-2"],
          );
          assert.deepEqual(seen.unexpected, []);
        }
        t.diagnostic(
          `old response gate released after second outcome; socket canceled=${first.aborted}; reload waits for Start; exactly one additional token; only second grant received; server side effects measured separately`,
        );
      });
    }
  }
}

async function recordedReady(page) {
  await page.locator("#audio-heard").click();
  await page.locator("#audio-step-mic.done").waitFor();
  await page.locator("#audio-step-camera.done").waitFor();
  await page.waitForFunction(
    () => globalThis.recordingFixtureDetectorCalls > 0,
  );
  assert.equal(await page.locator("#camera-skip").isVisible(), false);
  const restored = await page.locator("#recording-consent").isChecked();
  if (!restored)
    assert.equal(await page.locator("#audio-check-join").isDisabled(), true);
  await page.locator("#recording-consent").check();
  await page.waitForFunction(
    () => !document.querySelector("#audio-check-join").disabled,
  );
  return restored;
}

async function recordedRefused(page, selection) {
  if (selection === "strict") {
    await strictFailure(page, RECORDING_FIXTURE_REFUSAL);
    assert.equal(
      await page.locator("#audio-check-join").textContent(),
      "Retry media preflight",
    );
  } else {
    await page.waitForFunction(
      () => document.querySelector("#agent-state").textContent === "Offline",
    );
    assert.equal(await page.evaluate(() => window.roomJoins.length), 0);
  }
  assert.notEqual(
    await page.locator("#recording-state").textContent(),
    "Recording.",
  );
  if (selection === "strict")
    assert.equal(
      await page.evaluate(
        (key) => sessionStorage.getItem(key),
        STRICT_ASSIGNMENT_KEY,
      ),
      null,
    );
}

for (const selection of ["practice", "strict"]) {
  for (const identity of ["oauth", "self-declared"]) {
    test(`recording-enabled ${identity} ${selection} counts page lifecycle requests`, async (t) => {
      const ctx = await recordedPage(t, selection, identity);
      if (!ctx) return;
      const { page, seen, release, firstToken } = ctx;
      assert.equal(await recordedReady(page), false);
      assert.deepEqual(
        [seen.consents.length, seen.tokens.length, seen.starts.length],
        [0, 0, 0],
      );
      await doubleStart(page);
      await within(firstToken, "first /api/token request");
      assert.deepEqual(
        [seen.consents.length, seen.tokens.length, seen.starts.length],
        [1, 1, 0],
      );
      assert.equal(seen.tokens[0].interviewId, seen.consents[0]);
      assert.equal(seen.tokens[0].previousAssignment, undefined);
      assert.equal(
        seen.tokens[0].selectionMode,
        selection === "strict" ? "strict" : undefined,
      );
      assert.equal(
        seen.tokens[0].problemId,
        selection === "strict" ? undefined : "chargeback-pair-match",
      );
      release();
      if (identity === "oauth") {
        await page.waitForFunction(
          () =>
            document.querySelector("#recording-state").textContent ===
            "Recording.",
        );
        assert.deepEqual(seen.order.slice(0, 3), [
          "consent:recorded-1",
          "token:recorded-1",
          "recording:recorded-1",
        ]);
        await page.clock.install();
        await page.evaluate(() => {
          for (const handler of window.roomEvents.Reconnecting) handler();
          for (const handler of window.roomEvents.Reconnected) handler();
        });
        await page.clock.runFor(1000);
        assert.deepEqual(
          [seen.consents.length, seen.tokens.length, seen.starts.length],
          [1, 1, 1],
        );
        await page.clock.resume();
      } else {
        await recordedRefused(page, selection);
        assert.equal(seen.starts.length, 0);
        assert.equal(seen.recordingRequests, 0);
      }
      await page.reload({ waitUntil: "domcontentloaded" });
      const restored = await recordedReady(page);
      assert.equal(seen.tokens.length, 1, "reload waits for another Start");
      await page.locator("#audio-check-join").click();
      if (identity === "oauth") {
        await page.waitForFunction(
          () =>
            document.querySelector("#recording-state").textContent ===
            "Recording.",
        );
        assert.equal(seen.starts.length, 2);
        assert.equal(
          seen.tokens[1].previousAssignment,
          selection === "strict" ? "recorded-assignment-1" : undefined,
        );
        assert.notEqual(seen.consents[0], seen.consents[1]);
        assert.deepEqual(seen.order, [
          "consent:recorded-1",
          "token:recorded-1",
          "recording:recorded-1",
          "consent:recorded-2",
          "token:recorded-2",
          "recording:recorded-2",
        ]);
      } else {
        await recordedRefused(page, selection);
        assert.equal(seen.starts.length, 0);
        assert.equal(seen.recordingRequests, 0);
        assert.equal(seen.tokens[1].previousAssignment, undefined);
      }
      assert.deepEqual([seen.consents.length, seen.tokens.length], [2, 2]);
      assert.equal(seen.tokens[1].interviewId, seen.consents[1]);
      assert.deepEqual(seen.unexpected, []);
      t.diagnostic(
        `recording=on; session=${identity}; consent/token=1 each per Start; recording POSTs=${seen.starts.length}; replay batches observed=${seen.events}; restored checkbox=${restored}; detector unavailable, media/consent/room synthetic; no Egress; reconnect=${identity === "oauth" ? "0 additional requests" : "not admitted"}`,
      );
    });
  }
  test(`recording-enabled guest ${selection} redirects before consent or token`, async (t) => {
    const ctx = await recordedPage(t, selection, "guest");
    if (!ctx) return;
    await ctx.page.waitForURL(`${base}/`);
    assert.deepEqual(
      [
        ctx.seen.consents.length,
        ctx.seen.tokens.length,
        ctx.seen.starts.length,
        ctx.seen.events,
      ],
      [0, 0, 0, 0],
    );
    assert.deepEqual(ctx.seen.unexpected, []);
  });
}

for (const selection of ["practice", "strict"]) {
  test(`recording-enabled ${selection} consent refusal never asks for a token`, async (t) => {
    const ctx = await recordedPage(t, selection, "oauth", true);
    if (!ctx) return;
    await recordedReady(ctx.page);
    await ctx.page.locator("#audio-check-join").click();
    if (selection === "strict") {
      await strictFailure(ctx.page, "Fixture consent refused");
      assert.equal(
        await ctx.page.evaluate(
          (key) => sessionStorage.getItem(key),
          STRICT_ASSIGNMENT_KEY,
        ),
        null,
      );
    } else
      await ctx.page.waitForFunction(
        () => document.querySelector("#agent-state").textContent === "Offline",
      );
    assert.deepEqual(
      [
        ctx.seen.consents.length,
        ctx.seen.tokens.length,
        ctx.seen.starts.length,
      ],
      [1, 0, 0],
    );
    assert.equal(await ctx.page.evaluate(() => window.roomJoins.length), 0);
    assert.equal(ctx.seen.recordingRequests, 0);
    assert.notEqual(
      await ctx.page.locator("#recording-state").textContent(),
      "Recording.",
    );
    assert.deepEqual(ctx.seen.unexpected, []);
  });
}

// These session fixtures exercise page routing, not server authorization.
// OAuth and typed-account fixtures intentionally share the signed-in path:
// identityVerified is metadata, not a browser admission gate. The held response
// keeps both Start events ahead of any room connection.
for (const identity of ["oauth", "self-declared", "guest"]) {
  for (const selection of ["practice", "strict"]) {
    if (identity === "guest" && selection === "strict") continue;
    test(`${identity} ${selection} counts Start, reconnect and reload requests`, async (t) => {
      let requests = 0;
      let consents = 0;
      const bodies = [];
      let captured;
      const firstRequest = new Promise((resolve) => {
        captured = resolve;
      });
      let release;
      const held = new Promise((resolve) => {
        release = resolve;
      });
      t.after(() => release());
      const page = await interview(
        t,
        new URLSearchParams({
          problem: "chargeback-pair-match",
          selection,
        }),
        async (route) => {
          requests++;
          bodies.push(route.request().postDataJSON());
          captured();
          await held;
          await route.fulfill({
            json: {
              ...grant("chargeback-pair-match", `assignment-${requests}`),
              guest: identity === "guest",
            },
          });
        },
        browser,
        {
          signedIn: identity !== "guest",
          identityVerified: identity === "oauth",
          loginRequired: true,
          guestModeAvailable: true,
        },
      );
      if (!page) return;
      await page.route("**/api/interviews", (route) => {
        consents++;
        return route.fulfill({
          status: 500,
          json: { error: "Unexpected consent" },
        });
      });
      await readyToStart(page);
      await doubleStart(page);
      await within(firstRequest, "first /api/token request");
      assert.equal(requests, 1, "double Start while token response is held");
      assert.equal(await page.evaluate(() => window.roomJoins.length), 0);
      release();
      await page.waitForFunction(() => window.roomJoins.length === 1);
      await page.clock.install();
      await page.evaluate(async () => {
        for (const handler of window.roomEvents.Reconnecting) handler();
        for (const handler of window.roomEvents.Reconnected) handler();
        await new Promise(requestAnimationFrame);
      });
      await page.clock.runFor(1000);
      assert.equal(
        requests,
        1,
        "room event handlers do not renew the HTTP token",
      );
      assert.equal(await page.evaluate(() => window.roomJoins.length), 1);
      await page.clock.resume();
      await page.reload({ waitUntil: "domcontentloaded" });
      await readyToStart(page);
      assert.equal(requests, 1, "reload waits for another Start");
      await page.locator("#audio-check-join").click();
      await page.waitForFunction(() => window.roomJoins.length === 1);
      assert.equal(
        requests,
        2,
        "reload followed by Start requests a new token",
      );
      assert.equal(consents, 0, "recording off does not create consent rows");
      assert.ok(bodies.every((body) => body.interviewId === null));
      assert.equal(bodies[0].previousAssignment, undefined);
      assert.equal(
        bodies[1].previousAssignment,
        selection === "strict" ? "assignment-1" : undefined,
      );
      t.diagnostic(
        "recording=off; consent=none; receipt=" +
          (selection === "strict" ? "synthetic" : "none") +
          "; token requests: double Start=1, reconnect=0 additional, reload+Start=1 additional; transport stubbed",
      );
    });
  }
}
