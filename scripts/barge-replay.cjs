// Injected before page code: microphone tracks stay silent until the harness
// observes Speaking. Each acquisition has its own output track, as in a browser.
function installBargeReplay(wavBase64) {
  const media = navigator.mediaDevices;
  const original = media.getUserMedia.bind(media);
  const outputs = [];
  let context;
  let played = false;
  media.getUserMedia = async (constraints) => {
    const stream = await original(constraints);
    if (constraints?.audio) {
      context ??= new AudioContext();
      const destination = context.createMediaStreamDestination();
      for (const track of stream.getAudioTracks()) {
        stream.removeTrack(track);
        track.stop();
      }
      stream.addTrack(destination.stream.getAudioTracks()[0]);
      outputs.push(destination);
    }
    return stream;
  };
  const agentState = () =>
    document.querySelector("#agent-state")?.textContent ?? "";
  globalThis.replayBarge = async () => {
    function refuse(excluded, message) {
      globalThis.bargeReplayAttempt = {
        excluded,
        observedState: agentState(),
        attemptedAt: performance.now(),
        injectedAt: null,
      };
      throw new Error(message);
    }
    if (!agentState().includes("Speaking"))
      refuse("misaligned", "barge replay is misaligned: not Speaking");
    if (!context || !outputs.length || played)
      refuse("missing-microphone", "barge replay has no fresh microphone");
    const bytes = Uint8Array.from(atob(wavBase64), (ch) => ch.charCodeAt(0));
    let buffer;
    try {
      buffer = await context.decodeAudioData(bytes.buffer);
      await context.resume();
    } catch {
      refuse(
        "decode-or-resume-failed",
        "barge replay could not decode or resume audio",
      );
    }
    // Decoding/resume may have outlasted the speaking interval.
    const observedState = agentState();
    if (!observedState.includes("Speaking"))
      refuse("misaligned", "barge replay is misaligned after decoding");
    const source = context.createBufferSource();
    source.buffer = buffer;
    for (const destination of outputs) source.connect(destination);
    played = true;
    const injectedAt = performance.now();
    source.start();
    return (globalThis.bargeReplayAttempt = {
      observedState,
      injectedAt,
      sampleRate: buffer.sampleRate,
      duration: buffer.duration,
    });
  };
}
module.exports = { installBargeReplay };
