import { test } from "node:test";
import assert from "node:assert/strict";
import {
  createMediaSampler,
  assertJimAudio,
  installMediaProbe,
  summarizeJimAudio,
  mediaProbeSource,
} from "../../scripts/media-probe.cjs";

function fixture({
  counts = [0, 10, 20],
  rms = 0.1,
  stats = true,
  running = true,
  contextError = false,
  timeoutMs = 40,
  speakingWaitMs = timeoutMs,
  minimumWindowMs,
  onDelay = () => {},
} = {}) {
  let clock = 0;
  let index = 0;
  let current = true;
  let speaking = true;
  let cleaned = 0;
  let closed = 0;
  const context = {
    state: running ? "running" : "suspended",
    resume: async () => {},
    close: async () => {
      closed++;
    },
    createMediaStreamSource: () => ({
      connect: () => {},
      disconnect: () => {
        cleaned++;
      },
    }),
    createAnalyser: () => ({
      fftSize: 0,
      getFloatTimeDomainData: (values) => values.fill(rms),
      disconnect: () => {
        cleaned++;
      },
    }),
  };
  const track = {
    mediaStreamTrack: {},
    getRTCStatsReport: async () => {
      if (!stats) return;
      const value = counts[Math.min(index++, counts.length - 1)];
      return new Map([
        [
          "inbound",
          {
            type: "inbound-rtp",
            kind: "audio",
            id: "inbound",
            bytesReceived: value,
            packetsReceived: value,
          },
        ],
      ]);
    },
  };
  const sampler = createMediaSampler({
    now: () => clock,
    delay: async (ms) => {
      clock += ms;
      onDelay();
    },
    makeContext: () => {
      if (contextError) throw new Error("unavailable");
      return context;
    },
    makeStream: (input) => {
      assert.equal(input, track.mediaStreamTrack);
      return {};
    },
    timeoutMs,
    speakingWaitMs,
    minimumWindowMs,
    intervalMs: 10,
  });
  return {
    track,
    sampler,
    context,
    run: () =>
      sampler(
        track,
        7,
        () => current,
        () => speaking,
      ),
    changeTrack: () => {
      current = false;
    },
    quiet: () => {
      speaking = false;
    },
    speak: () => {
      speaking = true;
    },
    cleaned: () => cleaned,
    closed: () => closed,
  };
}

test("track receiver counters and decoded signal establish distinct evidence", async () => {
  const f = fixture();
  const result = await f.run();
  assert.equal(result.status, "ok");
  assert.equal(result.generation, 7);
  assert.equal(result.bytesReceived, 10);
  assert.equal(result.packetsReceived, 10);
  assert.ok(result.peakRms > 0.01);
  assert.equal(result.durationMs, 10);
  assert.equal(f.cleaned(), 2);
  assert.equal(f.closed(), 1);
  assert.doesNotThrow(() => assertJimAudio({ generations: [result] }));
});
for (const [options, status] of [
  [{ counts: [9, 9, 9] }, "transport-silent"],
  [{ rms: 0 }, "decoded-silent"],
  [{ stats: false }, "unavailable"],
  [{ running: false }, "unavailable"],
  [{ contextError: true }, "unavailable"],
]) {
  test(`controlled ${JSON.stringify(options)} reports ${status}`, async () => {
    const f = fixture(options);
    const result = await f.run();
    assert.equal(result.status, status);
    assert.throws(
      () => assertJimAudio({ generations: [result] }),
      new RegExp(status),
    );
    if (!options.contextError) assert.equal(f.closed(), 1);
  });
}

test("counter resets do not create a positive delta", async () => {
  const f = fixture({ counts: [100, 1, 1, 1] });
  const result = await f.run();
  assert.equal(result.status, "transport-silent");
  assert.equal(result.bytesReceived, 0);
});

test("a replaced track cannot contribute late receiver observations", async () => {
  const f = fixture();
  f.track.getRTCStatsReport = async () => {
    f.changeTrack();
    return new Map([
      [
        "live",
        {
          type: "inbound-rtp",
          kind: "audio",
          id: "live",
          bytesReceived: 100,
          packetsReceived: 10,
        },
      ],
    ]);
  };
  const result = await f.run();
  assert.equal(result.status, "superseded");
  assert.equal(result.statsSamples, 0);
  assert.equal(f.closed(), 1);
});

test("a replacement after one counter pair supersedes a quiet generation instead of failing it", async () => {
  let delays = 0;
  const f = fixture({
    rms: 0,
    timeoutMs: 100,
    onDelay: () => {
      if (++delays === 2) f.changeTrack();
    },
  });
  const result = await f.run();
  assert.equal(result.counterPairs, 1);
  assert.equal(result.status, "superseded");
  assert.equal(result.reason, "track-ended-before-signal");
  assert.equal(f.closed(), 1);
});

test("a slow resume neither delays receiver samples nor ends decoded sampling", async () => {
  let delays = 0;
  let running;
  const f = fixture({
    counts: [0, 10, 20, 30, 40, 50],
    timeoutMs: 100,
    onDelay: () => {
      if (++delays === 3) running();
    },
  });
  f.context.state = "suspended";
  f.context.resume = () =>
    new Promise((resolve) => {
      running = () => {
        f.context.state = "running";
        resolve();
      };
    });
  const result = await f.run();
  assert.equal(result.status, "ok");
  assert.ok(result.statsSamples >= 4);
  assert.equal(f.closed(), 1);
});

test("a generation that sampled a long window keeps its failure when its track ends", async () => {
  let delays = 0;
  const f = fixture({
    counts: [9],
    timeoutMs: 1000,
    minimumWindowMs: 100,
    onDelay: () => {
      if (++delays === 20) f.changeTrack();
    },
  });
  const result = await f.run();
  assert.equal(result.status, "transport-silent");
});

test("no Speaking interval produces no signal claim or audio context", async () => {
  const f = fixture();
  f.quiet();
  const result = await f.run();
  assert.equal(result.status, "unavailable");
  assert.equal(result.samples, 0);
  assert.equal(result.reason, "no-speaking-samples");
  assert.equal(result.peakRms, null);
  assert.equal(f.closed(), 0);
});

test("receiver timeout is bounded and releases its context", async () => {
  const f = fixture({ timeoutMs: 10 });
  f.track.getRTCStatsReport = () => new Promise(() => {});
  const result = await f.run();
  assert.equal(result.status, "unavailable");
  assert.equal(f.closed(), 1);
});

test("a good old generation cannot hide a silent resubscription", () => {
  assert.throws(
    () =>
      assertJimAudio({
        generations: [
          { status: "ok" },
          { generation: 2, status: "transport-silent", reason: "flat" },
        ],
      }),
    /generation 2/,
  );
  assert.throws(
    () => assertJimAudio({ generations: [] }),
    /no-attached-agent-track/,
  );
});

test("receiver identity resets and ambiguous streams cannot fabricate packet deltas", async () => {
  const f = fixture();
  let calls = 0;
  f.track.getRTCStatsReport = async () =>
    new Map([
      [
        "inbound",
        {
          type: "inbound-rtp",
          kind: "audio",
          id: `stream-${calls++}`,
          bytesReceived: calls * 100,
          packetsReceived: calls,
        },
      ],
    ]);
  assert.equal((await f.run()).status, "unavailable");
  const g = fixture();
  g.track.getRTCStatsReport = async () =>
    new Map(
      [1, 2].map((id) => [
        id,
        {
          type: "inbound-rtp",
          kind: "audio",
          id,
          bytesReceived: 100,
          packetsReceived: 10,
        },
      ]),
    );
  assert.equal((await g.run()).status, "unavailable");
});

test("setup, analyser failure and refused cleanup remain bounded unavailable observations", async () => {
  const f = fixture();
  f.context.createMediaStreamSource = () => {
    throw new Error("setup");
  };
  assert.equal((await f.run()).status, "unavailable");
  assert.equal(f.closed(), 1);
  const g = fixture();
  g.context.createAnalyser = () => ({
    disconnect: () => {},
    getFloatTimeDomainData: () => {
      throw new Error("read");
    },
  });
  assert.equal((await g.run()).status, "unavailable");
  assert.equal(g.closed(), 1);
  const h = fixture();
  h.context.close = async () => {
    throw new Error("already closed");
  };
  assert.equal((await h.run()).status, "ok");
});

test("public Room subscription hooks follow only Jim's attached track and keep generation outcomes", async (t) => {
  const { EventEmitter } = await import("node:events");
  const names = [
    "LivekitClient",
    "__createMediaSampler",
    "__finishJimAudioProbe",
    "__summarizeJimAudio",
    "document",
  ];
  const previous = names.map((name) => [
    name,
    Object.getOwnPropertyDescriptor(globalThis, name),
  ]);
  t.after(() => {
    for (const [name, descriptor] of previous) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  });
  const events = {
    TrackSubscribed: "sub",
    TrackUnsubscribed: "unsub",
    Disconnected: "disconnect",
  };
  globalThis.LivekitClient = { Room: EventEmitter, RoomEvent: events };
  let attached;
  globalThis.document = {
    querySelector: (selector) =>
      selector === "#jim-audio"
        ? { srcObject: { getAudioTracks: () => [attached] } }
        : { textContent: "Speaking" },
  };
  const seen = [];
  globalThis.__createMediaSampler =
    () => async (track, generation, isCurrent, isSpeaking) => {
      seen.push({ track, generation, isCurrent, isSpeaking });
      return {
        generation,
        status: "ok",
        bytesReceived: 10,
        packetsReceived: 1,
        peakRms: 0.1,
      };
    };
  globalThis.__summarizeJimAudio = summarizeJimAudio;
  installMediaProbe();
  const room = new globalThis.LivekitClient.Room();
  const first = { kind: "audio", mediaStreamTrack: {} };
  const unrelated = { kind: "audio", mediaStreamTrack: {} };
  const replacement = { kind: "audio", mediaStreamTrack: {} };
  attached = first.mediaStreamTrack;
  room.emit("sub", unrelated);
  room.emit("sub", first);
  await new Promise((resolve) => queueMicrotask(resolve));
  assert.equal(seen.length, 1);
  assert.equal(seen[0].isCurrent(), true);
  assert.equal(seen[0].isSpeaking(), true);
  attached = replacement.mediaStreamTrack;
  room.emit("sub", replacement);
  await new Promise((resolve) => queueMicrotask(resolve));
  room.emit("unsub", first);
  assert.equal(seen[0].isCurrent(), false);
  assert.equal(seen[1].isCurrent(), true);
  room.emit("disconnect");
  assert.equal(seen[1].isCurrent(), false);
  const result = await globalThis.__finishJimAudioProbe();
  assert.equal(result.generations.length, 2);
  assert.equal(result.bytesReceived, 20);
  assert.equal(globalThis.LivekitClient.Room, EventEmitter);
  installMediaProbe();
  const noisyRoom = new globalThis.LivekitClient.Room();
  for (let index = 0; index < 65; index++) {
    const track = { kind: "audio", mediaStreamTrack: {} };
    attached = track.mediaStreamTrack;
    noisyRoom.emit("sub", track);
    await new Promise((resolve) => queueMicrotask(resolve));
  }
  const overflow = await globalThis.__finishJimAudioProbe();
  assert.equal(overflow.generations.length, 65);
  assert.equal(overflow.status, "unavailable");
  assert.equal(
    overflow.generations.at(-1).reason,
    "subscription-observation-limit",
  );
  assert.equal(overflow.generations.at(-1).bytesReceived, null);
  assert.equal(overflow.generations.at(-1).packetsReceived, null);
});

test("Chromium measures real received WebRTC packets and decoded tone without provider credentials", async (t) => {
  const { launchChromium, root } = await import("./source.js");
  const { join } = await import("node:path");
  const { readFileSync } = await import("node:fs");
  const browser = await launchChromium({
    args: ["--autoplay-policy=no-user-gesture-required"],
  });
  if (!browser) return t.skip("playwright chromium unavailable");
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.route("http://localhost:31999/**", async (route) => {
    const script = route.request().url().endsWith("/vendor/livekit-client.js");
    await route.fulfill({
      status: 200,
      headers: {
        "content-type": script ? "text/javascript" : "text/html",
        "content-security-policy":
          "default-src 'self'; script-src 'self' blob: 'wasm-unsafe-eval'",
      },
      body: script
        ? readFileSync(join(root, "web/vendor/livekit-client.js"), "utf8")
        : '<html><body><script src="/vendor/livekit-client.js"></script></body></html>',
    });
  });
  await page.goto("http://localhost:31999/");
  await page.evaluate(mediaProbeSource());
  const sdkProbe = await page.evaluate(async () => {
    const room = new globalThis.LivekitClient.Room();
    const result = await globalThis.__finishJimAudioProbe();
    await room.disconnect();
    return result;
  });
  assert.equal(sdkProbe.status, "unavailable");
  assert.equal(sdkProbe.generations.length, 0);
  const result = await page.evaluate(async () => {
    const sender = new RTCPeerConnection();
    const receiver = new RTCPeerConnection();
    const context = new AudioContext();
    const oscillator = context.createOscillator();
    const destination = context.createMediaStreamDestination();
    oscillator.connect(destination);
    oscillator.start();
    sender.onicecandidate = (event) => {
      if (event.candidate) void receiver.addIceCandidate(event.candidate);
    };
    receiver.onicecandidate = (event) => {
      if (event.candidate) void sender.addIceCandidate(event.candidate);
    };
    // Bounded, so a loopback that never negotiates fails here and still
    // reaches the cleanup below instead of hanging the suite.
    const received = new Promise((resolve, reject) => {
      receiver.ontrack = resolve;
      setTimeout(() => reject(new Error("loopback track timeout")), 10000);
    });
    sender.addTrack(destination.stream.getAudioTracks()[0], destination.stream);
    try {
      await sender.setLocalDescription(await sender.createOffer());
      await receiver.setRemoteDescription(sender.localDescription);
      await receiver.setLocalDescription(await receiver.createAnswer());
      await sender.setRemoteDescription(receiver.localDescription);
      const { track, receiver: inbound } = await received;
      const audio = document.createElement("audio");
      audio.srcObject = new MediaStream([track]);
      document.body.append(audio);
      await audio.play();
      return await globalThis.__createMediaSampler()(
        {
          mediaStreamTrack: track,
          getRTCStatsReport: () => inbound.getStats(),
        },
        1,
        () => true,
        () => true,
      );
    } finally {
      oscillator.stop();
      destination.stream.getTracks().forEach((track) => track.stop());
      sender.close();
      receiver.close();
      await context.close();
    }
  });
  assert.equal(result.status, "ok", JSON.stringify(result));
  assert.ok(result.bytesReceived > 0);
  assert.ok(result.packetsReceived > 0);
  assert.ok(result.peakRms > 0.001);
});

test("non-Speaking gaps cannot bridge counter deltas", async () => {
  let ticks = 0;
  const f = fixture({
    counts: [0, 100, 100, 100],
    onDelay: () => {
      ticks++;
      if (ticks === 1) f.quiet();
      if (ticks === 2) f.speak();
    },
  });
  const result = await f.run();
  assert.equal(result.status, "transport-silent");
  assert.equal(result.bytesReceived, 0);
});

test("Speaking lost during a receiver await discards the sample and its baseline", async () => {
  let calls = 0;
  const f = fixture({
    onDelay: () => {
      if (calls === 2) f.speak();
    },
  });
  f.track.getRTCStatsReport = async () => {
    calls++;
    if (calls === 2) f.quiet();
    return new Map([
      [
        "inbound",
        {
          type: "inbound-rtp",
          kind: "audio",
          id: "inbound",
          bytesReceived: calls === 1 ? 0 : 100,
          packetsReceived: calls === 1 ? 0 : 10,
        },
      ],
    ]);
  };
  const result = await f.run();
  assert.equal(result.status, "transport-silent");
  assert.equal(result.bytesReceived, 0);
});

test("cancel discards a pending stats observation and closes the context", async () => {
  const f = fixture();
  let stopped = false;
  f.track.getRTCStatsReport = async () => {
    stopped = true;
    return new Map([
      [
        "inbound",
        {
          type: "inbound-rtp",
          kind: "audio",
          id: "inbound",
          bytesReceived: 100,
          packetsReceived: 10,
        },
      ],
    ]);
  };
  const result = await f.sampler(
    f.track,
    1,
    () => true,
    () => true,
    () => stopped,
  );
  assert.equal(result.statsSamples, 0);
  assert.equal(result.status, "unavailable");
  assert.equal(result.reason, "sampling-cancelled");
  assert.equal(f.closed(), 1);
});

test("waiting for Speaking does not spend the sampling window", async () => {
  let ticks = 0;
  const f = fixture({
    timeoutMs: 20,
    speakingWaitMs: 60,
    onDelay: () => {
      if (++ticks === 3) f.speak();
    },
  });
  f.quiet();
  const result = await f.run();
  assert.equal(result.status, "ok");
  assert.equal(result.durationMs, 40);
});

test("unknown aggregate observations remain null and superseded empty generations require a healthy successor", () => {
  const absent = summarizeJimAudio([
    {
      generation: 1,
      status: "unavailable",
      reason: "stats",
      bytesReceived: null,
      packetsReceived: null,
      peakRms: null,
    },
  ]);
  assert.equal(absent.bytesReceived, null);
  assert.equal(absent.packetsReceived, null);
  assert.equal(absent.peakRms, null);
  assert.equal(absent.reason, "stats");
  const superseded = {
    generation: 1,
    status: "superseded",
    reason: "replaced",
    bytesReceived: null,
    packetsReceived: null,
    peakRms: null,
  };
  const healthy = {
    generation: 2,
    status: "ok",
    bytesReceived: 10,
    packetsReceived: 1,
    peakRms: 0.1,
  };
  assert.equal(summarizeJimAudio([superseded, healthy]).status, "ok");
  assert.throws(
    () => assertJimAudio({ generations: [superseded] }),
    /superseded/,
  );
});
