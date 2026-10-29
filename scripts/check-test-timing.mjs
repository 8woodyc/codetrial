#!/usr/bin/env node
// Rejects the timing shapes that made browser tests fail on loaded CI runners
// while passing on a laptop, so a new one cannot land unannounced.
//
//   * A wall-clock bound built from `Date.now() +` and held in a variable named
//     like a deadline, limit, or expiry: it measures how busy the runner is,
//     not the page. Wait with `until` or `animationsFinished` from
//     tests/browser/wait.js, whose timeout is only a backstop that names what
//     hung.
//   * A fixed sleep, `waitForTimeout(...)` or `setTimeout(resolve, N)` with N of
//     50 ms or more, with no comment in the three lines above it or on its
//     line. A fixed sleep either proves an absence by outwaiting it (so it must
//     say what is being outwaited) or papers over a race (so it must not be
//     there).
//
// A bound that is deliberate carries `timing-ok:` and a reason in a comment on
// its line or the line above. Matching runs over the code with comments and
// string contents blanked, so a call split over several lines is still seen and
// quoted text neither triggers a finding nor passes for a comment or a marker.
// The scanner does not know regular-expression literals, so one holding a quote
// or a comment marker can blank the code after it; none of the tests does.
//
//   check-test-timing.mjs [DIR]    DIR defaults to tests/browser

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const dir = process.argv[2] ?? path.join(root, "tests", "browser");
const problems = [];

const bound =
  /\b\w*(?:deadline|limit|until|expir\w*|giveUp|stopAt)\w*\s*=\s*Date\.now\(\)\s*\+/gi;
const sleep =
  /\bwaitForTimeout\(|\bsetTimeout\(\s*(?:\w+|\(\s*\)\s*=>\s*\w+\(\s*\))\s*,\s*(?:[5-9]\d|\d{3,})\s*,?\s*\)/g;

// One global pattern for every comment and string token, so plain code is
// skipped by the regex engine instead of examined a character at a time.
const token =
  /\/\/[^\n]*|\/\*[\s\S]*?\*\/|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`/g;

// Returns `code`, the source with comments and string contents replaced by
// spaces (newlines kept, so offsets and lines still line up), and the sets of
// lines that carry a comment and that carry a `timing-ok:` marker.
function scan(source) {
  const commented = new Set();
  const marked = new Set();
  const blank = (text) => text.replace(/[^\n]/g, " ");
  let code = "";
  let last = 0;
  let line = 0;
  let counted = 0;
  for (const found of source.matchAll(token)) {
    const [text] = found;
    line += source.slice(counted, found.index).split("\n").length - 1;
    counted = found.index;
    code += source.slice(last, found.index);
    if (text[0] === "/") {
      const span = text.split("\n").length;
      for (let k = 0; k < span; k++) {
        commented.add(line + k);
        if (text.includes("timing-ok:")) marked.add(line + k);
      }
      code += blank(text);
    } else {
      code += text[0] + blank(text.slice(1, -1)) + text.at(-1);
    }
    last = found.index + text.length;
  }
  return { code: code + source.slice(last), commented, marked };
}

for (const name of fs.readdirSync(dir).sort()) {
  // wait.js is where the one sanctioned deadline lives.
  if (!name.endsWith(".js") || name === "wait.js") continue;
  const source = fs.readFileSync(path.join(dir, name), "utf8");
  // A file without any of these cannot produce a finding.
  if (!/Date\.now|waitForTimeout|setTimeout/.test(source)) continue;
  const { code, commented, marked } = scan(source);
  const lineOf = (offset) => code.slice(0, offset).split("\n").length - 1;
  const within = (lines, line, before) => {
    for (let k = Math.max(0, line - before); k <= line; k++) {
      if (lines.has(k)) return true;
    }
    return false;
  };
  for (const [pattern, report, needsComment] of [
    [bound, "wall-clock bound; use until() from wait.js", false],
    [sleep, "fixed sleep without a comment saying what it outwaits", true],
  ]) {
    for (const found of code.matchAll(pattern)) {
      const line = lineOf(found.index);
      if (within(marked, line, 1)) continue;
      if (needsComment && within(commented, line, 3)) continue;
      problems.push(`${name}:${line + 1}: ${report}`);
    }
  }
}

if (problems.length) {
  console.error(problems.join("\n"));
  process.exit(1);
}
