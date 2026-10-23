// Output choices shared by the preflight and Google Meet controls.
//
// Free of DOM and Web Audio types on purpose, like audio-check.js, so these
// decisions run under `node --test` instead of being asserted as source text;
// interview.js supplies the real nodes and the real `setSinkId`.

export const OUTPUT_NOTES = {
  unsupported:
    "This browser cannot choose an output device. Use the system default, or switch to Chrome.",
  listFailed:
    "Output devices could not be listed. Check the selected device or use the system default.",
  empty:
    "Specific output devices may appear after you allow microphone access. You can still use the system default.",
  refused:
    "That output device was refused. The previous output or system default is selected.",
};

/// Devices worth offering. Outputs only, and only those the browser has named:
/// before a `getUserMedia` grant it reports a placeholder whose `deviceId` is
/// empty, which renders as a real choice and then selects to "", so a candidate
/// picking it silently gets nothing.
export function outputOptions(devices, selectedId) {
  return [
    { deviceId: "", label: "System default", selected: !selectedId },
    ...devices
      .filter(
        (device) =>
          device.kind === "audiooutput" &&
          device.deviceId &&
          device.deviceId !== "default",
      )
      .map((device, index) => ({
        deviceId: device.deviceId,
        // Labels stay blank until a permission grant; number them so the list is
        // still usable rather than a column of empty rows.
        label: device.label || `Output ${index + 1}`,
        selected: device.deviceId === selectedId,
      })),
  ];
}
