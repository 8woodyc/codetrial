import assert from "node:assert/strict";
import { test } from "node:test";
import vm from "node:vm";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { installBargeReplay } = require("../../scripts/barge-replay.cjs");

function fixture() {
  let state = "Listening";
  let played = 0;
  let stopped = 0;
  let onDecode = () => {};
  const originalTrack = {
    kind: "audio",
    stop() {
      stopped++;
    },
  };
  const videoTrack = { kind: "video" };
  const replacement = { kind: "audio" };
  const tracks = [originalTrack, videoTrack];
  const stream = {
    getAudioTracks: () => tracks.filter((track) => track.kind === "audio"),
    removeTrack(track) {
      tracks.splice(tracks.indexOf(track), 1);
    },
    addTrack(track) {
      tracks.push(track);
    },
  };
  const sandbox = {
    navigator: { mediaDevices: { getUserMedia: async () => stream } },
    document: { querySelector: () => ({ textContent: state }) },
    atob: () => "wav",
    performance: { now: () => 123 },
    AudioContext: class {
      createMediaStreamDestination() {
        return { stream: { getAudioTracks: () => [replacement] } };
      }
      async decodeAudioData() {
        onDecode();
        return { duration: 1, sampleRate: 48000 };
      }
      async resume() {}
      createBufferSource() {
        return {
          connect() {},
          start() {
            played++;
          },
        };
      }
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(`(${installBargeReplay.toString()})("fixture")`, sandbox);
  return {
    sandbox,
    tracks,
    videoTrack,
    replacement,
    state: (value) => {
      state = value;
    },
    onDecode: (fn) => {
      onDecode = fn;
    },
    played: () => played,
    stopped: () => stopped,
  };
}

test("replay replaces only audio, remains silent until Speaking and starts once", async () => {
  const f = fixture();
  await f.sandbox.navigator.mediaDevices.getUserMedia({
    audio: true,
    video: true,
  });
  assert.equal(f.stopped(), 1);
  assert.deepEqual(f.tracks, [f.videoTrack, f.replacement]);
  assert.equal(f.played(), 0);
  await assert.rejects(f.sandbox.replayBarge(), /misaligned/);
  assert.equal(f.sandbox.bargeReplayAttempt.excluded, "misaligned");
  assert.equal(f.sandbox.bargeReplayAttempt.observedState, "Listening");
  f.state("Speaking");
  const metadata = await f.sandbox.replayBarge();
  assert.equal(metadata.observedState, "Speaking");
  assert.equal(metadata.injectedAt, 123);
  assert.equal(f.played(), 1);
  await assert.rejects(f.sandbox.replayBarge(), /fresh microphone/);
});

test("state change during decode excludes the trial without injecting", async () => {
  const f = fixture();
  await f.sandbox.navigator.mediaDevices.getUserMedia({ audio: true });
  f.state("Speaking");
  f.onDecode(() => f.state("Listening"));
  await assert.rejects(f.sandbox.replayBarge(), /misaligned after decoding/);
  assert.equal(f.played(), 0);
  assert.equal(f.sandbox.bargeReplayAttempt.excluded, "misaligned");
  assert.equal(f.sandbox.bargeReplayAttempt.injectedAt, null);
});
