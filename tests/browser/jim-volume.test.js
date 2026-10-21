import { test } from "node:test";
import assert from "node:assert/strict";
import { createJimVolume, JIM_VOLUME_KEY } from "../../web/audio-output.js";
import { loadInterview, memoryStorage } from "./source.js";

function fixture(stored = null, audio = { volume: 1, muted: false }) {
  const storage = memoryStorage();
  if (stored !== null) storage.setItem(JIM_VOLUME_KEY, stored);
  let current = audio;
  const volume = createJimVolume({ storage, element: () => current });
  return {
    volume,
    storage,
    audio,
    replace: (next) => {
      current = next;
      volume.apply();
    },
  };
}

test("routing hold and candidate mute remain independent through volumechange", () => {
  const audio = Object.assign(new EventTarget(), { volume: 1, muted: false });
  const f = fixture("37", audio);
  f.volume.setRoutingHeld(true);
  assert.equal(audio.muted, true);
  assert.equal(f.volume.muted, false);
  f.volume.setVolume(45);
  audio.muted = false;
  audio.dispatchEvent(new Event("volumechange"));
  assert.equal(audio.muted, true);
  f.volume.toggleMute();
  f.volume.setRoutingHeld(false);
  assert.equal(audio.muted, true);
  assert.equal(f.volume.muted, true);
  f.volume.toggleMute();
  assert.equal(audio.muted, false);
  assert.equal(audio.volume, 0.45);
  assert.equal(f.storage.getItem(JIM_VOLUME_KEY), "45");
});

for (const stored of [
  null,
  "",
  "0",
  "101",
  "-1",
  "NaN",
  "1.5",
  " 50 ",
  "1e2",
]) {
  test(`invalid stored volume ${JSON.stringify(stored)} defaults to 100`, () => {
    const f = fixture(stored);
    f.volume.apply();
    assert.equal(f.volume.level, 100);
    assert.equal(f.audio.volume, 1);
    assert.equal(f.audio.muted, false);
  });
}

test("positive volume survives reload but zero and mute do not", () => {
  const f = fixture("42");
  f.volume.apply();
  assert.equal(f.audio.volume, 0.42);
  f.volume.setVolume(23);
  f.volume.setVolume(0);
  assert.equal(f.volume.muted, true);
  assert.equal(f.audio.muted, true);
  assert.equal(f.storage.getItem(JIM_VOLUME_KEY), "23");
  const reloaded = createJimVolume({ storage: f.storage });
  assert.equal(reloaded.level, 23);
  assert.equal(reloaded.muted, false);
  f.volume.toggleMute();
  assert.equal(f.audio.muted, false);
  assert.equal(f.audio.volume, 0.23);
  f.volume.toggleMute();
  f.volume.reset();
  assert.equal(f.audio.volume, 1);
  assert.equal(f.audio.muted, false);
  assert.equal(f.storage.getItem(JIM_VOLUME_KEY), "100");
});

test("a single step up from a mute unmutes at the saved level, and a chosen value is kept", () => {
  const f = fixture("27");
  f.volume.setVolume(0);
  f.volume.setVolume(1);
  assert.equal(f.volume.muted, false);
  assert.equal(f.volume.level, 27);
  assert.equal(f.audio.volume, 0.27);
  assert.equal(f.storage.getItem(JIM_VOLUME_KEY), "27");
  f.volume.setVolume(40);
  assert.equal(f.volume.level, 40);
  assert.equal(f.storage.getItem(JIM_VOLUME_KEY), "40");
  f.volume.setVolume(0);
  f.volume.setVolume(60);
  assert.equal(f.volume.muted, false);
  assert.equal(f.volume.level, 60);
  assert.equal(f.storage.getItem(JIM_VOLUME_KEY), "60");
});

test("newly attached elements inherit attenuation and session mute", () => {
  const f = fixture("30");
  f.volume.toggleMute();
  const next = { volume: 1, muted: false };
  f.replace(next);
  assert.equal(next.volume, 0.3);
  assert.equal(next.muted, true);
});

for (const throws of [false, true]) {
  test(`unsupported volume ${throws ? "throwing" : "no-op"} setter preserves mute`, () => {
    const audio = { muted: false };
    Object.defineProperty(audio, "volume", {
      get: () => 1,
      set: () => {
        if (throws) throw new Error("unsupported");
      },
    });
    const f = fixture("30", audio);
    f.volume.apply();
    assert.equal(f.volume.supported, false);
    f.volume.toggleMute();
    assert.equal(audio.muted, true);
    f.volume.reset();
    assert.equal(audio.muted, false);
    assert.equal(f.volume.supported, false);
  });
}

test("invalid settings are ignored and blocked storage does not break playback preferences", () => {
  const f = fixture();
  for (const value of [-1, 101, NaN, 1.5, "50"]) f.volume.setVolume(value);
  assert.equal(f.volume.level, 100);
  const volume = createJimVolume({
    storage: {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    },
  });
  volume.setVolume(33);
  assert.equal(volume.level, 33);
  volume.reset();
  assert.equal(volume.level, 100);
});

test("interview attach, replacement and drop/resubscribe reapply volume and mute", () => {
  const own = {
    jimAudio: null,
    jimTrack: null,
    isCurrentAgent: () => true,
    isAvatarAnalyserTrack: () => false,
    audioPlayback: { play: () => Promise.resolve(), forget: () => {} },
    document: {
      createElement: () => ({
        volume: 1,
        muted: false,
        isConnected: false,
        remove() {
          this.isConnected = false;
        },
      }),
      body: {
        appendChild: (element) => {
          element.isConnected = true;
        },
      },
    },
  };
  own.jimVolume = createJimVolume({
    element: () => own.jimAudio,
    storage: memoryStorage(),
  });
  const { playRemoteAudio: play, dropRemoteAudio: drop } = loadInterview(
    ["playRemoteAudio", "dropRemoteAudio"],
    own,
    { stubMissing: true },
  );
  const track = () => ({
    kind: "audio",
    elements: [],
    attach(element) {
      this.elements.push(element);
      element.muted = false;
    },
    detach(only) {
      const removed = this.elements.filter(
        (element) => !only || element === only,
      );
      this.elements = this.elements.filter(
        (element) => !removed.includes(element),
      );
      return removed;
    },
  });
  own.jimVolume.setVolume(35);
  own.jimVolume.toggleMute();
  const first = track();
  play(first, {});
  assert.equal(own.jimAudio.volume, 0.35);
  assert.equal(own.jimAudio.muted, true);
  const original = own.jimAudio;
  const replacement = track();
  play(replacement, {});
  assert.equal(own.jimAudio, original);
  assert.equal(own.jimAudio.muted, true);
  drop(replacement);
  assert.equal(own.jimAudio, null);
  play(track(), {});
  assert.notEqual(own.jimAudio, original);
  assert.equal(own.jimAudio.volume, 0.35);
  assert.equal(own.jimAudio.muted, true);
});

test("unsupported setter UI hides its slider, keeps mute and moves focus before hiding", () => {
  const nodes = {
    jimVolumeRange: {
      value: "",
      setAttribute(name, value) {
        this[name] = value;
      },
    },
    jimVolumeRow: { hidden: false },
    jimVolumeValue: { textContent: "" },
    jimVolumeUnsupported: { hidden: true },
    jimMutedIndicator: { hidden: true },
    jimMute: {
      setAttribute(name, value) {
        this[name] = value;
      },
      focus() {
        own.document.activeElement = this;
      },
    },
  };
  const own = {
    nodes,
    jimVolume: { supported: false, muted: true, level: 30 },
    document: { activeElement: nodes.jimVolumeRange },
  };
  loadInterview(["renderJimVolume"], own).renderJimVolume();
  assert.equal(nodes.jimVolumeRow.hidden, true);
  assert.equal(nodes.jimVolumeUnsupported.hidden, false);
  assert.equal(nodes.jimMutedIndicator.hidden, false);
  assert.equal(nodes.jimMute["aria-pressed"], "true");
  assert.equal(own.document.activeElement, nodes.jimMute);
});

test("unsolicited LiveKit unmute and volume changes restore preferences without a room status event", () => {
  const audio = Object.assign(new EventTarget(), { volume: 1, muted: false });
  const f = fixture("32", audio);
  f.volume.toggleMute();
  audio.muted = false;
  audio.volume = 1;
  audio.dispatchEvent(new Event("volumechange"));
  assert.equal(audio.muted, true);
  assert.equal(audio.volume, 0.32);
  const next = Object.assign(new EventTarget(), { volume: 1, muted: false });
  f.replace(next);
  audio.muted = false;
  audio.dispatchEvent(new Event("volumechange"));
  assert.equal(audio.muted, false);
  assert.equal(next.muted, true);
  f.replace(null);
  next.muted = false;
  next.dispatchEvent(new Event("volumechange"));
  assert.equal(next.muted, false);
});
