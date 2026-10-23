// Browser playback cannot prove physical audibility through system effects.
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

/// The sample is a few seconds long and reports paused again once it ends,
/// so a slow runner can miss the !paused wait. Looping keeps it playing
/// until the stop under test, which pauses and rewinds it either way.
async function playLoopedSpeech(page) {
  await page
    .locator("#audio-test-voice")
    .evaluate((audio) => (audio.loop = true));
  await page.locator("#audio-test-speech").click();
  await page.waitForFunction(
    () => !document.querySelector("#audio-test-voice").paused,
  );
}

async function preflight(t, initialize) {
  const page = await browser.newPage();
  t.after(() => page.close());
  if (initialize) await page.addInitScript(initialize);
  await page.addInitScript(() => {
    const capture = navigator.mediaDevices.getUserMedia.bind(
      navigator.mediaDevices,
    );
    navigator.mediaDevices.getUserMedia = async (constraints) => {
      const stream = await capture(constraints);
      if (constraints.audio) window.testMicrophone = stream.getAudioTracks()[0];
      return stream;
    };
  });
  await page.goto(`${base}/interview.html?problem=chargeback-pair-match`);
  await page.waitForFunction(
    () => window.testMicrophone?.readyState === "live",
  );
  return page;
}

for (const reset of [false, true]) {
  for (const listing of ["empty", "placeholder", "failure"]) {
    test(`saved output survives a pre-grant ${listing} listing and ${reset ? "can return to default" : "routes both test sounds"}`, async (t) => {
      if (!browser) return t.skip("playwright chromium unavailable");
      const page = await browser.newPage();
      t.after(() => page.close());
      await page.addInitScript((listing) => {
        localStorage.setItem("codetrial:audioOutputId", "headphones");
        const capture = navigator.mediaDevices.getUserMedia.bind(
          navigator.mediaDevices,
        );
        navigator.mediaDevices.getUserMedia = async (constraints) => {
          if (constraints.audio)
            await new Promise((resolve) => {
              window.grantMicrophone = resolve;
            });
          const stream = await capture(constraints);
          if (constraints.audio) window.microphoneGranted = true;
          return stream;
        };
        navigator.mediaDevices.enumerateDevices = async () => {
          if (listing === "failure") throw new Error("Listing unavailable");
          if (window.microphoneGranted)
            return [
              {
                kind: "audiooutput",
                deviceId: "headphones",
                label: "Headphones",
              },
            ];
          return listing === "empty"
            ? []
            : [{ kind: "audiooutput", deviceId: "", label: "" }];
        };
        window.probeRoutes = [];
        HTMLMediaElement.prototype.setSinkId = async function (id) {
          window.probeRoutes.push(["speech", id]);
          Object.defineProperty(this, "sinkId", {
            configurable: true,
            value: id,
          });
        };
        AudioContext.prototype.setSinkId = async function (id) {
          window.probeRoutes.push(["tone", id]);
          Object.defineProperty(this, "sinkId", {
            configurable: true,
            value: id,
          });
        };
      }, listing);
      await page.goto(`${base}/interview.html?problem=chargeback-pair-match`);
      await page.waitForFunction(
        () =>
          window.grantMicrophone &&
          !document.querySelector("#preflight-output-note").hidden,
      );
      for (const selector of ["#preflight-output", "#meet-output-select"]) {
        assert.equal(await page.locator(selector).inputValue(), "headphones");
        assert.equal(
          await page.locator(`${selector} option:checked`).textContent(),
          "Saved output device",
        );
      }
      assert.equal(
        await page.evaluate(() =>
          localStorage.getItem("codetrial:audioOutputId"),
        ),
        "headphones",
      );
      await page.locator("#audio-test-tone").click();
      await page.waitForFunction(
        () =>
          document.querySelector("#audio-test-tone").textContent ===
          "Play it again",
      );
      assert.deepEqual(
        await page.evaluate(() =>
          window.probeRoutes.filter(([sound]) => sound === "tone"),
        ),
        [["tone", "headphones"]],
      );
      await page.locator("#audio-test-speech").click();
      await page.waitForFunction(
        () =>
          !document.querySelector("#audio-test-voice").paused ||
          document.querySelector("#audio-test-voice").ended,
      );
      assert.deepEqual(
        await page.evaluate(() =>
          window.probeRoutes.filter(([sound]) => sound === "speech"),
        ),
        [["speech", "headphones"]],
      );
      if (reset) {
        await page.locator("#preflight-output").selectOption("");
        for (const selector of ["#preflight-output", "#meet-output-select"])
          assert.equal(await page.locator(selector).inputValue(), "");
        assert.equal(
          await page.evaluate(() =>
            localStorage.getItem("codetrial:audioOutputId"),
          ),
          null,
        );
        await page.locator("#audio-test-speech").click();
        await page.waitForFunction(() =>
          window.probeRoutes.some(
            ([sound, id]) => sound === "speech" && id === "",
          ),
        );
      }
      await page.evaluate(() => window.grantMicrophone());
      await page.waitForFunction(() => window.microphoneGranted);
      if (listing !== "failure") {
        await page.waitForFunction(
          () =>
            document.querySelector("#preflight-output option[value=headphones]")
              ?.textContent === "Headphones",
        );
      }
      for (const selector of ["#preflight-output", "#meet-output-select"])
        assert.equal(
          await page.locator(selector).inputValue(),
          reset ? "" : "headphones",
        );
      assert.equal(
        await page.evaluate(() =>
          localStorage.getItem("codetrial:audioOutputId"),
        ),
        reset ? null : "headphones",
      );
    });
  }
}

test("preflight output defaults to the system and routes test sounds to the chosen device", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await preflight(t, () => {
    navigator.mediaDevices.enumerateDevices = async () => [
      { kind: "audiooutput", deviceId: "default", label: "Default speaker" },
      { kind: "audiooutput", deviceId: "headphones", label: "Headphones" },
      { kind: "audioinput", deviceId: "microphone", label: "Microphone" },
    ];
    window.outputRoutes = [];
    HTMLMediaElement.prototype.setSinkId = async function (id) {
      window.outputRoutes.push({ target: this.id, id });
      Object.defineProperty(this, "sinkId", { configurable: true, value: id });
    };
    AudioContext.prototype.setSinkId = async function (id) {
      window.outputRoutes.push({ target: "tone", id });
      Object.defineProperty(this, "sinkId", { configurable: true, value: id });
    };
  });
  await page.waitForFunction(
    () => document.querySelector("#preflight-output").options.length === 2,
  );
  assert.equal(await page.locator("#preflight-output").inputValue(), "");
  assert.deepEqual(
    await page.locator("#preflight-output option").allTextContents(),
    ["System default", "Headphones"],
  );
  await page.locator("#audio-heard").click();
  await page.waitForFunction(() =>
    document.querySelector("#audio-step-output").classList.contains("done"),
  );
  await page.locator("#preflight-output").selectOption("headphones");
  assert.equal(await page.locator("#audio-check-join").isDisabled(), true);
  assert.equal(await page.locator("#audio-heard").textContent(), "I heard it");
  await page.locator("#audio-test-tone").click();
  await page.waitForFunction(() =>
    window.outputRoutes.some(
      (route) => route.target === "tone" && route.id === "headphones",
    ),
  );
  await page.locator("#audio-test-speech").click();
  await page.waitForFunction(() =>
    window.outputRoutes.some(
      (route) =>
        route.target === "audio-test-voice" && route.id === "headphones",
    ),
  );
  await page.locator("#preflight-output").selectOption("");
  await page.locator("#audio-test-tone").click();
  await page.waitForFunction(() =>
    window.outputRoutes.some(
      (route) => route.target === "tone" && route.id === "",
    ),
  );
  await page.locator("#audio-test-speech").click();
  await page.waitForFunction(() =>
    window.outputRoutes.some(
      (route) => route.target === "audio-test-voice" && route.id === "",
    ),
  );
});

test("system-default test sounds leave already-default sinks unchanged", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await preflight(t, () => {
    window.routeAttempts = 0;
    const route = async () => {
      window.routeAttempts += 1;
    };
    HTMLMediaElement.prototype.setSinkId = route;
    AudioContext.prototype.setSinkId = route;
  });
  await page.locator("#audio-test-tone").click();
  await page.waitForFunction(
    () =>
      document.querySelector("#audio-test-tone").textContent ===
      "Play it again",
  );
  await page.locator("#audio-test-speech").click();
  await page.waitForFunction(
    () => document.querySelector("#audio-test-voice").ended,
  );
  assert.equal(await page.evaluate(() => window.routeAttempts), 0);
});

async function outputRoutingPage(t) {
  const page = await preflight(t, () => {
    navigator.mediaDevices.enumerateDevices = async () => [
      { kind: "audiooutput", deviceId: "default", label: "Default" },
      { kind: "audiooutput", deviceId: "headphones", label: "Headphones" },
      { kind: "audiooutput", deviceId: "speakers", label: "Speakers" },
    ];
  });
  await page.evaluate(async () => {
    window.outputApi = await import("./audio-output.js");
    window.jimRoutes = [];
    window.mockJim = {
      sinkId: "",
      async setSinkId(id) {
        window.jimRoutes.push(id);
        if (window.holdRoute)
          await new Promise((resolve) => {
            window.releaseRoute = resolve;
          });
        if (id === window.refusedOutput) throw new Error("Device refused");
        this.sinkId = id;
      },
    };
    window.outputApi.initAudioOutput({
      nodes: {
        meetOutputSelect: document.querySelector("#meet-output-select"),
        meetOutputRow: document.querySelector("#meet-output-row"),
        meetOutputNote: document.querySelector("#meet-output-note"),
        preflightOutput: document.querySelector("#preflight-output"),
        preflightOutputNote: document.querySelector("#preflight-output-note"),
      },
      jimAudioElement: () => window.mockJim,
    });
    await window.outputApi.refreshAudioOutputs();
  });
  return page;
}

test("both output selectors share choices and reset preflight confirmation", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await outputRoutingPage(t);
  assert.deepEqual(
    await page.locator("#meet-output-select option").allTextContents(),
    await page.locator("#preflight-output option").allTextContents(),
  );
  await page.locator("#preflight-output").selectOption("headphones");
  await page.waitForFunction(() => window.mockJim.sinkId === "headphones");
  assert.equal(
    await page.locator("#meet-output-select").inputValue(),
    "headphones",
  );
  await page.locator("#audio-heard").click();
  await page.evaluate(() => {
    const select = document.querySelector("#meet-output-select");
    select.value = "speakers";
    select.dispatchEvent(new Event("change"));
  });
  await page.waitForFunction(() => window.mockJim.sinkId === "speakers");
  assert.equal(
    await page.locator("#preflight-output").inputValue(),
    "speakers",
  );
  assert.equal(await page.locator("#audio-heard").textContent(), "I heard it");
});

test("a refused Jim route restores both selectors to the actual previous sink", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await outputRoutingPage(t);
  await page.locator("#preflight-output").selectOption("speakers");
  await page.waitForFunction(() => window.mockJim.sinkId === "speakers");
  await page.evaluate(() => {
    window.refusedOutput = "headphones";
  });
  await page.locator("#preflight-output").selectOption("headphones");
  await page.waitForFunction(() =>
    document
      .querySelector("#preflight-output-note")
      .textContent.includes("refused"),
  );
  for (const id of ["preflight-output", "meet-output-select"]) {
    assert.equal(await page.locator(`#${id}`).inputValue(), "speakers");
  }
  assert.equal(
    await page.evaluate(() => window.outputApi.audioOutputId()),
    "speakers",
  );
  assert.equal(await page.evaluate(() => window.mockJim.sinkId), "speakers");
  assert.equal(
    await page.locator("#preflight-output-note").textContent(),
    "That output device was refused. The previous output or system default is selected.",
  );
  const refusal = await page.locator("#preflight-output-note").textContent();
  await page.evaluate(async () => {
    await window.outputApi.refreshAudioOutputs();
    window.previousOutputOption =
      document.querySelector("#preflight-output").options[0];
    navigator.mediaDevices.dispatchEvent(new Event("devicechange"));
  });
  await page.waitForFunction(() => !window.previousOutputOption.isConnected);
  assert.equal(
    await page.locator("#preflight-output-note").textContent(),
    refusal,
  );
  assert.equal(
    await page.evaluate(() => localStorage.getItem("codetrial:audioOutputId")),
    "speakers",
  );
  await page.evaluate(() => {
    window.holdRoute = true;
  });
  await page.locator("#preflight-output").selectOption("");
  await page.waitForFunction(() => Boolean(window.releaseRoute));
  assert.match(
    await page.locator("#preflight-output-note").textContent(),
    /refused/,
  );
  await page.evaluate(() => {
    window.holdRoute = false;
    window.releaseRoute();
  });
  await page.waitForFunction(() => window.mockJim.sinkId === "");
  assert.equal(await page.locator("#preflight-output-note").isHidden(), true);
});

test("a pending Jim route finishes before the newest choice and cannot roll it back", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await outputRoutingPage(t);
  await page.evaluate(() => {
    window.holdRoute = true;
  });
  await page.locator("#preflight-output").selectOption("headphones");
  await page.waitForFunction(() => Boolean(window.releaseRoute));
  await page.locator("#preflight-output").selectOption("speakers");
  assert.deepEqual(await page.evaluate(() => window.jimRoutes), ["headphones"]);
  await page.evaluate(() => {
    window.holdRoute = false;
    window.releaseRoute();
  });
  await page.waitForFunction(() => window.mockJim.sinkId === "speakers");
  assert.deepEqual(await page.evaluate(() => window.jimRoutes), [
    "headphones",
    "speakers",
  ]);
  for (const id of ["preflight-output", "meet-output-select"]) {
    assert.equal(await page.locator(`#${id}`).inputValue(), "speakers");
  }
});

test("a refused Jim route returns to default when its previous sink was unplugged", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await outputRoutingPage(t);
  await page.locator("#preflight-output").selectOption("headphones");
  await page.waitForFunction(() => window.mockJim.sinkId === "headphones");
  await page.evaluate(() => {
    window.refusedOutput = "speakers";
    window.holdRoute = true;
  });
  await page.locator("#preflight-output").selectOption("speakers");
  await page.waitForFunction(() => Boolean(window.releaseRoute));
  await page.evaluate(() => {
    navigator.mediaDevices.enumerateDevices = async () => [
      { kind: "audiooutput", deviceId: "speakers", label: "Speakers" },
    ];
    navigator.mediaDevices.dispatchEvent(new Event("devicechange"));
  });
  await page.waitForFunction(
    () => document.querySelector("#preflight-output").options.length === 2,
  );
  await page.evaluate(() => {
    window.holdRoute = false;
    window.releaseRoute();
  });
  await page.waitForFunction(() =>
    document
      .querySelector("#preflight-output-note")
      .textContent.includes("refused"),
  );
  for (const id of ["preflight-output", "meet-output-select"]) {
    assert.equal(await page.locator(`#${id}`).inputValue(), "");
  }
  assert.equal(await page.evaluate(() => window.mockJim.sinkId), "");
  assert.equal(await page.evaluate(() => window.outputApi.audioOutputId()), "");
});

test("output survives a replaced Jim element with storage blocked", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await outputRoutingPage(t);
  await page.evaluate(() => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new Error("Storage blocked");
      },
    });
  });
  await page.locator("#preflight-output").selectOption("headphones");
  await page.waitForFunction(() => window.mockJim.sinkId === "headphones");
  await page.evaluate(async () => {
    window.mockJim = {
      sinkId: "",
      async setSinkId(id) {
        this.sinkId = id;
      },
    };
    await window.outputApi.applyAudioOutput(window.outputApi.audioOutputId());
  });
  assert.equal(await page.evaluate(() => window.mockJim.sinkId), "headphones");
  await page.addInitScript(() => {
    Object.defineProperty(window, "localStorage", {
      get() {
        throw new Error("Storage blocked");
      },
    });
  });
  await page.reload();
  await page.waitForFunction(
    () => document.querySelector("#preflight-output").options.length === 3,
  );
  assert.equal(await page.locator("#preflight-output").inputValue(), "");
});

test("output choices survive reload and System default clears the saved preference", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await outputRoutingPage(t);
  await page.evaluate(() => localStorage.setItem("reload-check", "available"));
  await page.locator("#preflight-output").selectOption("headphones");
  await page.waitForFunction(() => window.mockJim.sinkId === "headphones");
  await page.evaluate(() => {
    const select = document.querySelector("#meet-output-select");
    select.value = "speakers";
    select.dispatchEvent(new Event("change"));
  });
  await page.waitForFunction(() => window.mockJim.sinkId === "speakers");
  assert.equal(
    await page.evaluate(() => localStorage.getItem("codetrial:audioOutputId")),
    "speakers",
  );
  await page.reload();
  await page.waitForFunction(
    () => document.querySelector("#preflight-output").options.length === 3,
  );
  assert.equal(
    await page.locator("#preflight-output").inputValue(),
    "speakers",
  );
  assert.equal(
    await page.locator("#meet-output-select").inputValue(),
    "speakers",
  );
  assert.equal(
    await page.evaluate(() => localStorage.getItem("reload-check")),
    "available",
  );
  await page.locator("#preflight-output").selectOption("");
  assert.equal(
    await page.evaluate(() => localStorage.getItem("codetrial:audioOutputId")),
    null,
  );
  await page.reload();
  await page.waitForFunction(
    () => document.querySelector("#preflight-output").options.length === 3,
  );
  assert.equal(await page.locator("#preflight-output").inputValue(), "");
  assert.equal(await page.locator("#meet-output-select").inputValue(), "");
});

test("a saved output that is no longer listed falls back to System default", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await preflight(t, () => {
    localStorage.setItem("codetrial:audioOutputId", "removed");
    navigator.mediaDevices.enumerateDevices = async () => [
      { kind: "audiooutput", deviceId: "headphones", label: "Headphones" },
    ];
  });
  await page.waitForFunction(
    () => document.querySelector("#preflight-output").options.length === 2,
  );
  await page.waitForFunction(
    () => localStorage.getItem("codetrial:audioOutputId") === null,
  );
  assert.equal(await page.locator("#preflight-output").inputValue(), "");
  assert.equal(await page.locator("#meet-output-select").inputValue(), "");
  assert.equal(
    await page.evaluate(() => localStorage.getItem("codetrial:audioOutputId")),
    null,
  );
});

for (const superseded of ["before routing starts", "during routing"]) {
  test(`output settlement follows newer choices ${superseded}`, async (t) => {
    if (!browser) return t.skip("playwright chromium unavailable");
    const page = await outputRoutingPage(t);
    await page.evaluate((when) => {
      window.holdRoute = true;
      window.outputSettled = false;
      void window.outputApi.applyAudioOutput("headphones");
      window.outputApi.audioOutputSettled().then(() => {
        window.outputSettled = true;
      });
      if (when === "before routing starts")
        void window.outputApi.applyAudioOutput("speakers");
    }, superseded);
    await page.waitForFunction(() => Boolean(window.releaseRoute));
    if (superseded === "during routing") {
      await page.evaluate(() => {
        void window.outputApi.applyAudioOutput("speakers");
        window.releaseRoute();
      });
      await page.waitForFunction(() => window.jimRoutes.length === 2);
    }
    assert.equal(await page.evaluate(() => window.outputSettled), false);
    await page.evaluate(() => {
      void window.outputApi.applyAudioOutput("");
      window.releaseRoute();
    });
    await page.waitForFunction(() => window.jimRoutes.at(-1) === "");
    assert.equal(await page.evaluate(() => window.outputSettled), false);
    await page.evaluate(() => {
      window.holdRoute = false;
      window.releaseRoute();
    });
    await page.waitForFunction(() => window.outputSettled);
    assert.equal(await page.evaluate(() => window.mockJim.sinkId), "");
  });
}

test("output permission hint appears without specific devices and clears when they appear", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await preflight(t, () => {
    navigator.mediaDevices.enumerateDevices = async () => [
      { kind: "audiooutput", deviceId: "", label: "" },
    ];
  });
  await page.waitForFunction(() =>
    document
      .querySelector("#preflight-output-note")
      .textContent.includes("microphone access"),
  );
  assert.deepEqual(
    await page.locator("#preflight-output option").allTextContents(),
    ["System default"],
  );
  for (const id of ["preflight-output-note", "meet-output-note"]) {
    assert.equal(
      await page.locator(`#${id}`).evaluate((note) => note.hidden),
      false,
    );
  }
  await page.evaluate(() => {
    navigator.mediaDevices.enumerateDevices = async () => [
      { kind: "audiooutput", deviceId: "headphones", label: "Headphones" },
    ];
    navigator.mediaDevices.dispatchEvent(new Event("devicechange"));
  });
  await page.waitForFunction(
    () => document.querySelector("#preflight-output").options.length === 2,
  );
  for (const id of ["preflight-output-note", "meet-output-note"]) {
    assert.equal(
      await page.locator(`#${id}`).evaluate((note) => note.hidden),
      true,
    );
  }
});

test("unsupported output selection keeps system-default playback available", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await preflight(t, () => {
    localStorage.setItem("codetrial:audioOutputId", "headphones");
    delete HTMLMediaElement.prototype.setSinkId;
  });
  assert.equal(await page.locator("#preflight-output").isDisabled(), true);
  assert.match(
    await page.locator("#preflight-output-note").textContent(),
    /system default/,
  );
  await page.locator("#audio-test-speech").click();
  await page.waitForFunction(
    () => document.querySelector("#audio-test-voice").ended,
  );
  assert.equal(await page.locator("#audio-heard").textContent(), "I heard it");
});

test("output enumeration recovers and ignores an older failed refresh", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await preflight(t, () => {
    navigator.mediaDevices.enumerateDevices = async () => {
      throw new Error("Device list unavailable");
    };
  });
  await page.waitForFunction(() =>
    document
      .querySelector("#preflight-output-note")
      .textContent.includes("could not be listed"),
  );
  await page.evaluate(() => {
    window.lists = [];
    window.holdLists = true;
    const devices = [
      { kind: "audiooutput", deviceId: "speaker", label: "Speaker" },
    ];
    navigator.mediaDevices.enumerateDevices = () =>
      window.holdLists
        ? new Promise((resolve, reject) => {
            window.lists.push({ resolve, reject });
          })
        : Promise.resolve(devices);
    navigator.mediaDevices.dispatchEvent(new Event("devicechange"));
    window.staleLists = [...window.lists];
    navigator.mediaDevices.dispatchEvent(new Event("devicechange"));
    for (const request of window.lists.slice(window.staleLists.length))
      request.resolve(devices);
    window.holdLists = false;
  });
  await page.waitForFunction(
    () => document.querySelector("#preflight-output").options.length === 2,
  );
  await page.evaluate(async () => {
    for (const request of window.staleLists)
      request.reject(new Error("Stale failure"));
    await Promise.resolve();
  });
  assert.equal(await page.locator("#preflight-output-note").isHidden(), true);
  assert.deepEqual(
    await page.locator("#preflight-output option").allTextContents(),
    ["System default", "Speaker"],
  );
});

test("removing the selected output restores default and requires confirmation again", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await preflight(t, () => {
    navigator.mediaDevices.enumerateDevices = async () => [
      { kind: "audiooutput", deviceId: "headphones", label: "Headphones" },
    ];
  });
  await page.waitForFunction(
    () => document.querySelector("#preflight-output").options.length === 2,
  );
  await page.locator("#preflight-output").selectOption("headphones");
  await page.locator("#audio-heard").click();
  await page.waitForFunction(() =>
    document.querySelector("#audio-step-output").classList.contains("done"),
  );
  await page.evaluate(() => {
    navigator.mediaDevices.enumerateDevices = async () => [];
    navigator.mediaDevices.dispatchEvent(new Event("devicechange"));
  });
  await page.waitForFunction(
    () => document.querySelector("#preflight-output").options.length === 1,
  );
  assert.equal(await page.locator("#preflight-output").inputValue(), "");
  assert.equal(await page.locator("#audio-heard").textContent(), "I heard it");
  assert.equal(await page.locator("#audio-check-join").isDisabled(), true);
  assert.match(
    await page.locator("#preflight-output-note").textContent(),
    /microphone access/,
  );
  assert.equal(
    await page
      .locator("#preflight-output-note")
      .evaluate((note) => note.hidden),
    false,
  );
});

test("a pending tone route cannot play after selecting away and back", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await preflight(t, () => {
    navigator.mediaDevices.enumerateDevices = async () => [
      { kind: "audiooutput", deviceId: "headphones", label: "Headphones" },
    ];
    window.routes = [];
    window.tones = [];
    AudioContext.prototype.setSinkId = async function (id) {
      window.routes.push(id);
      if (window.routes.length === 1)
        await new Promise((resolve) => {
          window.releaseRoute = resolve;
        });
      this.testSink = id;
      Object.defineProperty(this, "sinkId", { configurable: true, value: id });
    };
    const createOscillator = AudioContext.prototype.createOscillator;
    AudioContext.prototype.createOscillator = function () {
      const oscillator = createOscillator.call(this);
      const start = oscillator.start.bind(oscillator);
      oscillator.start = () => {
        window.tones.push(this.testSink);
        start();
      };
      return oscillator;
    };
  });
  await page.waitForFunction(
    () => document.querySelector("#preflight-output").options.length === 2,
  );
  await page.locator("#preflight-output").selectOption("headphones");
  await page.locator("#audio-test-tone").click();
  await page.waitForFunction(() => Boolean(window.releaseRoute));
  await page.locator("#preflight-output").selectOption("");
  await page.locator("#preflight-output").selectOption("headphones");
  await page.locator("#audio-test-tone").click();
  assert.deepEqual(await page.evaluate(() => window.tones), []);
  await page.evaluate(() => window.releaseRoute());
  await page.waitForFunction(() => window.tones.length > 0);
  assert.deepEqual(await page.evaluate(() => window.tones), ["headphones"]);
  assert.deepEqual(await page.evaluate(() => window.routes), ["headphones"]);
});

for (const sound of ["tone", "speech"]) {
  test(`a refused ${sound} output can be retried without confirming it`, async (t) => {
    if (!browser) return t.skip("playwright chromium unavailable");
    const page = await preflight(t, () => {
      navigator.mediaDevices.enumerateDevices = async () => [
        { kind: "audiooutput", deviceId: "headphones", label: "Headphones" },
      ];
      window.refuseOutput = true;
      window.routedOutput = false;
      const route = async () => {
        if (window.refuseOutput)
          throw new DOMException("Device refused", "NotAllowedError");
        window.routedOutput = true;
      };
      HTMLMediaElement.prototype.setSinkId = route;
      AudioContext.prototype.setSinkId = route;
    });
    await page.waitForFunction(
      () => document.querySelector("#preflight-output").options.length === 2,
    );
    await page.locator("#preflight-output").selectOption("headphones");
    await page.locator(`#audio-test-${sound}`).click();
    await page.waitForFunction(() =>
      document
        .querySelector("#audio-check-status")
        .textContent.includes("Could not"),
    );
    assert.equal(
      await page.locator("#audio-heard").textContent(),
      "I heard it",
    );
    await page.evaluate(() => {
      window.refuseOutput = false;
    });
    await page.locator(`#audio-test-${sound}`).click();
    await page.waitForFunction(() => window.routedOutput);
    assert.equal(
      await page.locator("#audio-heard").textContent(),
      "I heard it",
    );
  });
}

test("preflight speech replays during capture and still requires the candidate's confirmation", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await preflight(t);
  const response = await page.request.get(`${base}/audio/test-voice.wav`);
  assert.equal(response.status(), 200);
  assert.match(response.headers()["content-type"], /^audio\/wav/);
  const signal = await page.evaluate(async () => {
    const response = await fetch("./audio/test-voice.wav");
    const buffer = await new OfflineAudioContext(1, 1, 48000).decodeAudioData(
      await response.arrayBuffer(),
    );
    let peak = 0;
    for (const value of buffer.getChannelData(0))
      peak = Math.max(peak, Math.abs(value));
    return { peak, duration: buffer.duration };
  });
  assert.ok(signal.peak > 0.01, "the shipped speech sample contains a signal");
  assert.ok(signal.duration > 1 && signal.duration < 10);
  for (let count = 0; count < 3; count++) {
    await page.locator("#audio-test-speech").click();
    await page.waitForFunction(
      () => document.querySelector("#audio-test-voice").ended,
    );
    assert.equal(
      await page.evaluate(() => window.testMicrophone.readyState),
      "live",
    );
    assert.equal(
      await page
        .locator("#audio-step-output")
        .evaluate((node) => node.classList.contains("done")),
      false,
    );
    assert.equal(await page.locator("#audio-check-join").isDisabled(), true);
  }
  await page.locator("#audio-heard").click();
  await page.waitForFunction(() =>
    document.querySelector("#audio-step-output").classList.contains("done"),
  );
});

test("preflight speech reports a rejected play and lets the candidate retry", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await preflight(t);
  await page.evaluate(() => {
    document.querySelector("#audio-test-voice").play = () =>
      Promise.reject(new DOMException("Autoplay blocked", "NotAllowedError"));
  });
  await page.locator("#audio-test-speech").click();
  await page.waitForFunction(() =>
    document
      .querySelector("#audio-check-status")
      .textContent.includes("Could not play"),
  );
  assert.equal(await page.locator("#audio-test-speech").isEnabled(), true);
  assert.equal(await page.locator("#audio-heard").textContent(), "I heard it");
  await page.evaluate(() => {
    delete document.querySelector("#audio-test-voice").play;
  });
  await page.locator("#audio-test-speech").click();
  await page.waitForFunction(
    () => document.querySelector("#audio-test-voice").ended,
  );
  assert.equal(
    await page.locator("#audio-test-voice").evaluate((audio) => audio.error),
    null,
  );
});

test("preflight retries a failed speech load without confirming output", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await preflight(t);
  let requests = 0;
  await page.route("**/audio/test-voice.wav", (route) => {
    requests++;
    return requests === 1
      ? route.fulfill({ status: 404, body: "not found" })
      : route.continue();
  });
  await page.locator("#audio-test-speech").click();
  await page.waitForFunction(
    () => document.querySelector("#audio-test-voice").error?.code === 4,
  );
  await page.waitForFunction(() =>
    document
      .querySelector("#audio-check-status")
      .textContent.includes("Could not"),
  );
  assert.equal(await page.locator("#audio-test-speech").isEnabled(), true);
  assert.equal(requests, 1);
  await page.locator("#audio-test-speech").click();
  await page.waitForFunction(
    () => document.querySelector("#audio-test-voice").ended,
  );
  assert.ok(requests > 1, "retry fetches the speech asset again");
  assert.equal(
    await page.locator("#audio-test-voice").evaluate((audio) => audio.error),
    null,
  );
  assert.equal(
    await page
      .locator("#audio-step-output")
      .evaluate((node) => node.classList.contains("done")),
    false,
  );
});

test("switching to the tone cancels pending speech without reporting a playback failure", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await preflight(t);
  let releaseSample;
  const heldSample = new Promise((resolve) => {
    releaseSample = resolve;
  });
  await page.route("**/audio/test-voice.wav", async (route) => {
    await heldSample;
    await route.continue();
  });
  try {
    await page.locator("#audio-test-speech").click();
    assert.equal(await page.locator("#audio-test-speech").isDisabled(), true);
    await page.locator("#audio-test-tone").click();
    await page.waitForFunction(
      () => !document.querySelector("#audio-test-speech").disabled,
    );
    assert.equal(
      await page.locator("#audio-test-voice").evaluate((audio) => audio.paused),
      true,
    );
    assert.doesNotMatch(
      await page.locator("#audio-check-status").textContent(),
      /Could not/,
    );
  } finally {
    releaseSample();
  }
});

test("leaving the preflight stops speech before navigation", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await preflight(t);
  let releaseLobby;
  const heldLobby = new Promise((resolve) => {
    releaseLobby = resolve;
  });
  await page.route(`${base}/`, async (route) => {
    await heldLobby;
    await route.continue();
  });
  try {
    await playLoopedSpeech(page);
    const stopped = await page.evaluate(() => {
      document.querySelector("#audio-check-leave").click();
      const audio = document.querySelector("#audio-test-voice");
      return { paused: audio.paused, time: audio.currentTime };
    });
    assert.deepEqual(stopped, { paused: true, time: 0 });
  } finally {
    releaseLobby();
  }
  await page.waitForURL(`${base}/`);
});

test("starting the interview stops preflight speech while keeping the microphone", async (t) => {
  if (!browser) return t.skip("playwright chromium unavailable");
  const page = await preflight(t);
  await page.route("**/api/token", (route) =>
    route.fulfill({ status: 503, body: "local test" }),
  );
  await page.locator("#camera-skip").click();
  await page.locator("#audio-heard").click();
  await page.waitForFunction(
    () => !document.querySelector("#audio-check-join").disabled,
  );
  await playLoopedSpeech(page);
  const stopped = await page.evaluate(() => {
    document.querySelector("#audio-check-join").click();
    const audio = document.querySelector("#audio-test-voice");
    return {
      paused: audio.paused,
      time: audio.currentTime,
      microphone: window.testMicrophone.readyState,
    };
  });
  assert.deepEqual(stopped, { paused: true, time: 0, microphone: "live" });
  assert.equal(await page.locator("#audio-check").isHidden(), true);
});
