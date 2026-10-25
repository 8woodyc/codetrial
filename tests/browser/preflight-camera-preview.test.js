import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_RUNTIME_CONFIG,
  FAKE_SPEECH_CAPTURE,
  startStaticServer,
} from "./source.js";

let browser;
let server;
let base;

before(async () => {
  try {
    const { chromium } = await import("playwright");
    browser = await chromium.launch({
      args: [
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
        FAKE_SPEECH_CAPTURE,
      ],
    });
  } catch (error) {
    if (process.env.CI) throw error;
    return;
  }

  ({ server, base } = await startStaticServer({
    runtimeConfig: DEFAULT_RUNTIME_CONFIG,
  }));
});

after(async () => {
  await browser?.close();
  await new Promise((closed) => (server ? server.close(closed) : closed()));
});

// These lifecycle checks need a speaking mic to pass preflight. Keep the real
// camera tracks while making microphone samples independent of headless audio.
async function provideMicrophoneSignal(page) {
  await page.addInitScript(() => {
    const NativeAudioContext = window.AudioContext;
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
  });
}

test("preflight shows a live camera preview", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");

  const page = await browser.newPage();
  t.after(() => page.close());

  await page.goto(`${base}/interview.html?problem=chargeback-pair-match`);

  // Wait until the existing camera check reports that the track is ready.
  await page.waitForFunction(() =>
    document.querySelector("#audio-step-camera").classList.contains("done"),
  );

  await page.waitForFunction(() => {
    const video = document.querySelector("#camera-preview");

    return (
      video?.srcObject instanceof MediaStream &&
      video.videoWidth > 0 &&
      video.videoHeight > 0
    );
  });

  const preview = page.locator("#camera-preview");

  assert.equal(await preview.count(), 1);
  assert.equal(await preview.isVisible(), true);

  const videoState = await preview.evaluate((video) => ({
    hasStream: video.srcObject instanceof MediaStream,
    hasLiveTrack: video.srcObject?.getVideoTracks()[0]?.readyState === "live",
    hasFrames: video.videoWidth > 0 && video.videoHeight > 0,
    isMuted: video.muted,
  }));

  assert.equal(videoState.hasStream, true);
  assert.equal(videoState.hasLiveTrack, true);
  assert.equal(videoState.hasFrames, true);
  assert.equal(videoState.isMuted, true);
});

test("preflight clears and restores the camera preview when skipped and re-enabled", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");

  const page = await browser.newPage();
  t.after(() => page.close());

  await page.goto(`${base}/interview.html?problem=chargeback-pair-match`);

  // Wait for the initial camera preview.
  await page.waitForFunction(() => {
    const video = document.querySelector("#camera-preview");
    return (
      video?.srcObject instanceof MediaStream &&
      video.videoWidth > 0 &&
      video.videoHeight > 0
    );
  });

  // Skip the camera.
  await page.locator("#camera-skip").click();

  await page.waitForFunction(() => {
    const video = document.querySelector("#camera-preview");
    return video.srcObject === null;
  });

  assert.equal(
    await page.locator(".camera-preview-container").isVisible(),
    false,
  );

  assert.equal(
    await page.locator("#camera-preview").evaluate((video) => video.srcObject),
    null,
  );

  // Re-enable the camera.
  await page.locator("#camera-skip").click();

  await page.waitForFunction(() => {
    const video = document.querySelector("#camera-preview");
    return (
      video?.srcObject instanceof MediaStream &&
      video.srcObject.getVideoTracks()[0]?.readyState === "live" &&
      video.videoWidth > 0 &&
      video.videoHeight > 0
    );
  });

  assert.equal(await page.locator("#camera-preview").isVisible(), true);
});

test("starting the interview clears the camera preview", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");

  const page = await browser.newPage();
  t.after(() => page.close());
  await provideMicrophoneSignal(page);

  await page.goto(`${base}/interview.html?problem=chargeback-pair-match`);

  await page.waitForFunction(() => {
    const video = document.querySelector("#camera-preview");

    return (
      video?.srcObject instanceof MediaStream &&
      video.videoWidth > 0 &&
      video.videoHeight > 0
    );
  });

  // Prevent the test from connecting to a real interview.
  await page.route("**/api/token", (route) =>
    route.fulfill({ status: 503, body: "local test" }),
  );

  await page.locator("#audio-heard").click();

  await page.waitForFunction(
    () => !document.querySelector("#audio-check-join").disabled,
  );

  await page.locator("#audio-check-join").click();

  const result = await page.evaluate(() => ({
    previewCleared:
      document.querySelector("#camera-preview").srcObject === null,
    previewHidden: document.querySelector(".camera-preview-container").hidden,
  }));

  assert.equal(result.previewCleared, true);
  assert.equal(result.previewHidden, true);
});
test("Meet presentation keeps the preflight preview until joining", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");

  const page = await browser.newPage();
  t.after(() => page.close());
  await provideMicrophoneSignal(page);

  await page.addInitScript(() => {
    const capture = navigator.mediaDevices.getUserMedia.bind(
      navigator.mediaDevices,
    );

    navigator.mediaDevices.getUserMedia = async (constraints) => {
      const stream = await capture(constraints);

      if (constraints.video) {
        window.testCameraTrack = stream.getVideoTracks()[0];
      }

      return stream;
    };
  });

  await page.goto(`${base}/interview.html?problem=chargeback-pair-match`);

  await page.waitForFunction(() => {
    const video = document.querySelector("#camera-preview");

    return (
      video?.srcObject instanceof MediaStream &&
      video.videoWidth > 0 &&
      video.videoHeight > 0
    );
  });

  // Enable Meet presentation mode while the preview is still active.
  await page.locator("#meet-presentation").check();

  assert.equal(await page.locator("#camera-preview").isVisible(), true);

  // Avoid connecting to a real interview.
  await page.route("**/api/token", (route) =>
    route.fulfill({ status: 503, body: "local test" }),
  );

  await page.locator("#audio-heard").click();

  await page.waitForFunction(
    () => !document.querySelector("#audio-check-join").disabled,
  );

  await page.locator("#audio-check-join").click();

  await page.waitForFunction(
    () => window.testCameraTrack?.readyState === "ended",
  );

  const result = await page.evaluate(() => ({
    previewCleared:
      document.querySelector("#camera-preview").srcObject === null,
    previewHidden: document.querySelector(".camera-preview-container").hidden,
    cameraTrackState: window.testCameraTrack?.readyState,
  }));
  assert.equal(result.previewCleared, true);
  assert.equal(result.previewHidden, true);
  assert.equal(result.cameraTrackState, "ended");
});
test("camera preview hides on mute and returns on unmute", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");

  const page = await browser.newPage();
  t.after(() => page.close());

  await page.goto(`${base}/interview.html?problem=chargeback-pair-match`);

  await page.waitForFunction(() => {
    const video = document.querySelector("#camera-preview");

    return (
      video?.srcObject instanceof MediaStream &&
      video.videoWidth > 0 &&
      video.videoHeight > 0
    );
  });

  const result = await page.evaluate(async () => {
    const video = document.querySelector("#camera-preview");
    const container = document.querySelector(".camera-preview-container");
    const track = video.srcObject.getVideoTracks()[0];

    // Simulate a temporarily unavailable camera.
    Object.defineProperty(track, "muted", {
      configurable: true,
      value: true,
    });
    track.dispatchEvent(new Event("mute"));

    const hiddenWhileMuted = container.hidden && video.srcObject === null;

    // Simulate the camera becoming available again.
    Object.defineProperty(track, "muted", {
      configurable: true,
      value: false,
    });
    track.dispatchEvent(new Event("unmute"));

    const restoredAfterUnmute =
      !container.hidden && video.srcObject?.getVideoTracks()[0] === track;

    return { hiddenWhileMuted, restoredAfterUnmute };
  });

  assert.equal(result.hiddenWhileMuted, true);
  assert.equal(result.restoredAfterUnmute, true);
});
test("skipping the camera prevents old track events from restoring the preview", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");

  const page = await browser.newPage();
  t.after(() => page.close());

  await page.goto(`${base}/interview.html?problem=chargeback-pair-match`);

  await page.waitForFunction(() => {
    const video = document.querySelector("#camera-preview");

    return (
      video?.srcObject instanceof MediaStream &&
      video.videoWidth > 0 &&
      video.videoHeight > 0
    );
  });

  // Keep a reference to the original track before skipping the camera.
  await page.evaluate(() => {
    window.oldCameraTrack = document
      .querySelector("#camera-preview")
      .srcObject.getVideoTracks()[0];
  });

  await page.locator("#camera-skip").click();

  await page.waitForFunction(
    () => document.querySelector("#camera-preview").srcObject === null,
  );

  const result = await page.evaluate(() => {
    // A late event from the old track must not restore the preview.
    window.oldCameraTrack.dispatchEvent(new Event("unmute"));

    const video = document.querySelector("#camera-preview");
    const container = document.querySelector(".camera-preview-container");

    return {
      previewCleared: video.srcObject === null,
      previewHidden: container.hidden,
      oldTrackEnded: window.oldCameraTrack.readyState === "ended",
    };
  });

  assert.equal(result.previewCleared, true);
  assert.equal(result.previewHidden, true);
  assert.equal(result.oldTrackEnded, true);
});
