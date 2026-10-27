import assert from "node:assert/strict";

// Real MediaStream tracks with deterministic microphone samples exercise the
// existing readiness gate without depending on headless audio scheduling.
// The room transport is fake; reload still discards the whole page state.
export function mediaAndRoom() {
  const NativeAudioContext = window.AudioContext;
  let sink;
  window.AudioContext = class extends NativeAudioContext {
    createAnalyser() {
      const analyser = super.createAnalyser();
      analyser.getByteTimeDomainData = (samples) => {
        for (let index = 0; index < samples.length; index++)
          samples[index] = index % 2 ? 200 : 60;
      };
      return analyser;
    }
  };
  navigator.mediaDevices.getUserMedia = async (constraints) => {
    if (constraints.video) {
      if (globalThis.CODETRIAL_RECORDING_ENABLED !== true)
        throw new DOMException("No camera", "NotFoundError");
      const canvas = document.createElement("canvas");
      canvas.getContext("2d").fillRect(0, 0, 1, 1);
      return canvas.captureStream();
    }
    sink ??= new NativeAudioContext();
    return sink.createMediaStreamDestination().stream;
  };
  window.roomJoins = [];
  window.roomEvents = {};
  window.LivekitClient = {
    RoomEvent: new Proxy({}, { get: (_, key) => key }),
    Room: class {
      remoteParticipants = new Map();
      localParticipant = {
        publishTrack: async () => {},
        publishData: async () => {},
      };
      on(event, callback) {
        (window.roomEvents[event] ??= []).push(callback);
        return this;
      }
      off(event, callback) {
        window.roomEvents[event] = (window.roomEvents[event] ?? []).filter(
          (handler) => handler !== callback,
        );
        return this;
      }
      async connect(url, token) {
        window.roomJoins.push({ url, token });
      }
      async disconnect() {}
    },
  };
}

export async function openInterview(
  { browser, base },
  t,
  query,
  handler,
  context = browser,
  session = { signedIn: true, loginRequired: true },
) {
  if (!browser) {
    t.skip("playwright chromium unavailable");
    return null;
  }
  if (context === browser) {
    context = await browser.newContext();
    t.after(() => context.close());
  }
  const page = await context.newPage();
  t.after(() => page.close());
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  page.on("dialog", (dialog) => dialog.accept());
  await page.route("**/vendor/livekit-client.js", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: "",
    }),
  );
  await page.route("**/api/session", (route) =>
    route.fulfill({
      json: session,
    }),
  );
  await page.route("**/api/token", handler);
  await page.addInitScript(mediaAndRoom);
  await page.goto(`${base}/interview.html?${query}`);
  return page;
}

export async function strictFailure(page, message) {
  await page.waitForFunction(
    (text) =>
      document.querySelector("#audio-check-status").textContent === text,
    message,
  );
  assert.equal(await page.locator("#audio-check").isVisible(), true);
  assert.equal(await page.locator("#audio-check-leave").isEnabled(), true);
  assert.equal(await page.locator("#editor").isDisabled(), true);
  assert.equal(await page.locator("#run-tests").isDisabled(), true);
  assert.equal(
    await page.locator("#agent-state").textContent(),
    "Not connected",
  );
  assert.doesNotMatch(
    await page.locator("#presence-banner").textContent(),
    /offline|personalized evaluation|still work the problem/i,
  );
  assert.equal(await page.evaluate(() => window.roomJoins.length), 0);
  assert.doesNotMatch(
    await page.locator("#transcript-panel").textContent(),
    /Offline (?:mode|practice)/,
  );
}

export function grant(page, receipt, room = receipt) {
  return {
    problemPage: page,
    previousAssignment: receipt,
    roomName: room,
    serverUrl: "wss://example.test",
    token: room,
    durationMin: 30,
  };
}
