// The sampler is also injected into Chromium. Keep it independent of Node and
// LiveKit internals: getRTCStatsReport is the public, track-specific receiver API.
function createMediaSampler({
  now = () => performance.now(),
  delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  makeContext = () =>
    new (globalThis.AudioContext || globalThis.webkitAudioContext)(),
  makeStream = (track) => new MediaStream([track]),
  timeoutMs = 10000,
  speakingWaitMs = timeoutMs,
  intervalMs = 100,
  rmsFloor = 0.001,
  minimumWindowMs = 1000,
} = {}) {
  return async function sample(
    track,
    generation,
    isCurrent,
    isSpeaking,
    isStopped = () => false,
  ) {
    const began = now();
    const result = {
      generation,
      durationMs: 0,
      samples: 0,
      statsSamples: 0,
      bytesReceived: 0,
      packetsReceived: 0,
      peakRms: null,
      status: "unavailable",
      reason: null,
    };
    let context, source, analyser;
    let windowBegan = null;
    let previous = null;
    let transportPairs = 0;
    // Both counters grow only with a counter pair, and peakRms is set only by
    // a decoded sample, so these three carry the whole proof.
    const proven = () =>
      result.bytesReceived > 0 &&
      result.packetsReceived > 0 &&
      result.peakRms > rmsFloor;
    const bounded = async (promise) => {
      let timer;
      try {
        return await Promise.race([
          promise,
          new Promise((_, reject) => {
            timer = setTimeout(
              () => reject(new Error("timeout")),
              Math.max(
                1,
                Math.min(500, timeoutMs - (now() - (windowBegan ?? began))),
              ),
            );
          }),
        ]);
      } finally {
        clearTimeout(timer);
      }
    };
    try {
      while (
        now() - (windowBegan ?? began) <
          (windowBegan === null ? speakingWaitMs : timeoutMs) &&
        isCurrent() &&
        !isStopped()
      ) {
        if (!isSpeaking()) {
          previous = null;
          await delay(intervalMs);
          continue;
        }
        windowBegan ??= now();
        result.samples++;
        if (!context) {
          try {
            context = makeContext();
            // Not awaited: a slow resume must neither hold up receiver samples
            // nor end decoded ones. The analyser is read once the context runs.
            Promise.resolve()
              .then(() => context.resume())
              .catch(() => {});
            source = context.createMediaStreamSource(
              makeStream(track.mediaStreamTrack),
            );
            analyser = context.createAnalyser();
            analyser.fftSize = 1024;
            source.connect(analyser);
          } catch {}
        }
        let stats;
        try {
          stats = await bounded(
            Promise.resolve().then(() => track.getRTCStatsReport?.()),
          );
        } catch {}
        if (!isCurrent() || isStopped()) break;
        if (!isSpeaking()) {
          previous = null;
          continue;
        }
        const inbound = [];
        stats?.forEach((entry) => {
          if (
            entry.type === "inbound-rtp" &&
            (entry.kind === "audio" || entry.mediaType === "audio") &&
            Number.isFinite(entry.bytesReceived) &&
            entry.bytesReceived >= 0 &&
            Number.isFinite(entry.packetsReceived) &&
            entry.packetsReceived >= 0
          )
            inbound.push(entry);
        });
        // A receiver should expose one inbound audio stream. Ambiguous reports
        // cannot prove which stream reached this track.
        const current = inbound.length === 1 ? inbound[0] : null;
        if (current) {
          result.statsSamples++;
          if (
            previous &&
            previous.id === current.id &&
            current.bytesReceived >= previous.bytesReceived &&
            current.packetsReceived >= previous.packetsReceived
          ) {
            result.bytesReceived +=
              current.bytesReceived - previous.bytesReceived;
            result.packetsReceived +=
              current.packetsReceived - previous.packetsReceived;
            transportPairs++;
          }
          previous = {
            id: current.id,
            bytesReceived: current.bytesReceived,
            packetsReceived: current.packetsReceived,
          };
        } else {
          previous = null;
        }
        if (analyser && context.state === "running") {
          const values = new Float32Array(analyser.fftSize);
          analyser.getFloatTimeDomainData(values);
          const rms = Math.sqrt(
            values.reduce((sum, value) => sum + value * value, 0) /
              values.length,
          );
          if (!Number.isFinite(rms)) throw new Error("invalid decoded samples");
          result.peakRms = Math.max(result.peakRms ?? 0, rms);
        }
        if (proven()) break;
        await delay(intervalMs);
      }
      if (isStopped()) {
        result.reason = "sampling-cancelled";
      } else if (
        !isCurrent() &&
        !proven() &&
        (windowBegan === null || now() - windowBegan < minimumWindowMs)
      ) {
        // Losing the track early cuts the window short, and a fresh analyser
        // reads near zero at first. A replacement is judged on its own; with
        // none, the summary fails on this generation being the last. A longer
        // window is judged on its evidence, so a reconnect cannot hide it.
        result.status = "superseded";
        result.reason = transportPairs
          ? "track-ended-before-signal"
          : "track-ended-before-counter-pair";
      } else if (result.samples === 0) {
        result.reason = "no-speaking-samples";
      } else if (!transportPairs) {
        result.reason = "receiver-stats-unavailable";
      } else if (result.peakRms === null) {
        result.reason = "decoded-signal-unavailable";
      } else if (!result.bytesReceived || !result.packetsReceived) {
        result.status = "transport-silent";
        result.reason = "audio-packets-did-not-increase";
      } else if (result.peakRms <= rmsFloor) {
        result.status = "decoded-silent";
        result.reason = "packets-arrived-without-decoded-signal";
      } else {
        result.status = "ok";
      }
    } catch {
      result.reason = "sampling-failed";
    } finally {
      try {
        source?.disconnect();
      } catch {}
      try {
        analyser?.disconnect();
      } catch {}
      if (context)
        await bounded(Promise.resolve().then(() => context.close())).catch(
          () => {},
        );
      result.durationMs = Math.max(0, now() - began);
      result.counterPairs = transportPairs;
      if (!transportPairs) {
        result.bytesReceived = null;
        result.packetsReceived = null;
      }
    }
    return result;
  };
}

// Installed after navigation, while the preflight still gates room creation.
// Observe public Room events and sample only the track the page attaches as Jim.
function installMediaProbe() {
  const sdk = globalThis.LivekitClient;
  const OriginalRoom = sdk.Room;
  const sample = globalThis.__createMediaSampler({ speakingWaitMs: 120000 });
  let generation = 0;
  let active = null;
  let stopped = false;
  let accepting = true;
  let overflow = false;
  const jobs = [];
  // Every way a track stops being Jim's moves the generation, which is what
  // ends its sampler.
  const retire = () => {
    active = null;
    generation++;
  };
  const speaking = () =>
    document.querySelector("#agent-state")?.textContent.includes("Speaking");
  sdk.Room = class extends OriginalRoom {
    constructor(...args) {
      super(...args);
      this.on(sdk.RoomEvent.TrackSubscribed, (track) => {
        if (!accepting || track.kind !== "audio") return;
        // App listeners attach Jim after this listener. A microtask sees that
        // choice without guessing agent identity or retaining participant data.
        queueMicrotask(() => {
          if (!accepting) return;
          const element = document.querySelector("#jim-audio");
          if (
            !element?.srcObject
              ?.getAudioTracks()
              .includes(track.mediaStreamTrack)
          )
            return;
          if (jobs.length >= 64) {
            overflow = true;
            retire();
            return;
          }
          active = track;
          const mine = ++generation;
          jobs.push(
            sample(
              track,
              mine,
              () => mine === generation,
              speaking,
              () => stopped,
            ),
          );
        });
      });
      this.on(sdk.RoomEvent.TrackUnsubscribed, (track) => {
        if (active === track) retire();
      });
      this.on(sdk.RoomEvent.Disconnected, retire);
    }
  };
  globalThis.__finishJimAudioProbe = async (cancel = false) => {
    accepting = false;
    if (cancel) stopped = true;
    sdk.Room = OriginalRoom;
    const generations = await Promise.all(jobs);
    if (overflow)
      generations.push({
        generation,
        status: "unavailable",
        reason: "subscription-observation-limit",
        bytesReceived: null,
        packetsReceived: null,
        peakRms: null,
      });
    return globalThis.__summarizeJimAudio(
      generations,
      document.querySelector("#audio-playback-notice")?.hidden === false,
    );
  };
}

function summarizeJimAudio(generations, playbackBlocked = null) {
  const last = generations.at(-1);
  const failure =
    generations.find((entry) => !["ok", "superseded"].includes(entry.status)) ??
    (last?.status === "ok" ? null : last);
  const measured = (field) =>
    generations.map((entry) => entry[field]).filter(Number.isFinite);
  const sum = (field) =>
    measured(field).reduce((total, value) => (total ?? 0) + value, null);
  const peaks = measured("peakRms");
  return {
    status: failure?.status ?? (last ? "ok" : "unavailable"),
    reason: failure?.reason ?? (last ? null : "no-attached-agent-track"),
    failedGeneration: failure?.generation ?? null,
    playbackBlocked,
    bytesReceived: sum("bytesReceived"),
    packetsReceived: sum("packetsReceived"),
    peakRms: peaks.length ? Math.max(...peaks) : null,
    generations,
  };
}

function mediaProbeSource() {
  return `globalThis.__createMediaSampler = ${createMediaSampler.toString()}; globalThis.__summarizeJimAudio = ${summarizeJimAudio.toString()}; (${installMediaProbe.toString()})();`;
}

function assertJimAudio(result) {
  const verdict = summarizeJimAudio(result?.generations ?? []);
  if (verdict.status !== "ok")
    throw new Error(
      `Jim audio ${verdict.status}: ${verdict.reason} (generation ${verdict.failedGeneration ?? "none"})`,
    );
}
module.exports = {
  createMediaSampler,
  installMediaProbe,
  summarizeJimAudio,
  mediaProbeSource,
  assertJimAudio,
};
