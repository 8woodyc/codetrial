// Keep complete diagnostic lines before the browser harness trims its stderr tail.
const fs = require("node:fs");
const { StringDecoder } = require("node:string_decoder");

class FloorCapture {
  constructor(path) {
    this.path = path;
    this.pending = "";
    this.decoder = new StringDecoder("utf8");
    this.discarding = false;
    this.fd = null;
  }

  push(chunk) {
    const text = Buffer.isBuffer(chunk) ? this.decoder.write(chunk) : chunk;
    for (const part of text.match(/[^\n]*\n|[^\n]+$/g) ?? []) {
      const complete = part.endsWith("\n");
      if (!this.discarding) {
        this.pending += part;
        if (this.pending.length > 4096) this.discarding = true;
      }
      if (!complete) continue;
      // One whole line per append write, so writers sharing the file never
      // interleave. The page cache keeps it if this process is killed.
      if (!this.discarding && this.pending.startsWith("codetrial floor ")) {
        this.fd ??= fs.openSync(this.path, "a", 0o600);
        fs.writeSync(this.fd, this.pending);
      }
      this.pending = "";
      this.discarding = false;
    }
  }

  close() {
    if (this.fd !== null) fs.closeSync(this.fd);
    this.fd = null;
  }
}

function readFloorRecords(path) {
  return fs
    .readFileSync(path, "utf8")
    .split("\n")
    .slice(0, -1)
    .filter((line) => line.startsWith("codetrial floor "))
    .map((line) =>
      Object.fromEntries(
        line
          .split(" ")
          .slice(2)
          .map((field) => field.split("=")),
      ),
    );
}

function assertInterruptedOverlap(path, room) {
  if (
    !readFloorRecords(path).some(
      (fields) =>
        fields.room === room &&
        fields.kind === "overlap" &&
        fields.outcome === "interrupted" &&
        /^\d+$/.test(fields.overlap_ms),
    )
  ) {
    throw new Error(
      "barge replay has no measured interrupted floor overlap; missing or misaligned activity",
    );
  }
}

module.exports = { FloorCapture, assertInterruptedOverlap, readFloorRecords };

// The server owns dispatched-agent stderr. Follow from byte zero until cleanup,
// including the bytes written after the browser exits or reports a failure.
if (require.main === module) {
  const [source, target, doneFile] = process.argv.slice(2);
  const capture = new FloorCapture(target);
  let offset = 0;
  const parentPid = process.ppid;
  let stopDeadline = null;
  const buffer = Buffer.alloc(65536);
  function poll() {
    if (!fs.existsSync(source)) return;
    const fd = fs.openSync(source, "r");
    try {
      let count;
      while ((count = fs.readSync(fd, buffer, 0, buffer.length, offset))) {
        offset += count;
        capture.push(buffer.subarray(0, count));
      }
    } finally {
      fs.closeSync(fd);
    }
  }
  const timer = setInterval(() => {
    poll();
    if (
      (doneFile && fs.existsSync(doneFile)) ||
      process.ppid !== parentPid ||
      (stopDeadline !== null && Date.now() >= stopDeadline)
    )
      finish();
  }, 50);
  function finish() {
    clearInterval(timer);
    poll();
    process.exit(0);
  }
  function interrupted() {
    if (!doneFile) return finish();
    // A group signal also reaches the server. Let shell cleanup wait for that
    // server before declaring capture complete; bound an abandoned cleanup.
    stopDeadline ??= Date.now() + 5000;
  }
  process.on("SIGTERM", interrupted);
  process.on("SIGINT", interrupted);
  poll();
}
