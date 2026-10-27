import { after, before, describe, test } from "node:test";
import assert from "node:assert/strict";
import { execFile, execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import {
  generateHarness,
  parseCompilerResults,
} from "../../web/compiler-explorer.js";
import { launchChromium, startStaticServer } from "./source.js";

const repeated = "Returned tree must not reach a node twice.";
const cycle = "Returned tree must not contain a cycle.";
const leaf = [1, 1];
const branch = [0, 1];
const fixtures = [
  { type: "nextTree", shape: "self", error: repeated },
  { type: "nextTree", shape: "ancestor", error: repeated },
  { type: "nextTree", shape: "shared", error: repeated },
  { type: "nextTree", shape: "equal", expected: [[7], [7, 7]] },
  { type: "quadTree", shape: "self", error: cycle },
  { type: "quadTree", shape: "ancestor", error: cycle },
  {
    type: "quadTree",
    shape: "equal",
    expected: [branch, leaf, leaf, leaf, leaf],
  },
  {
    type: "quadTree",
    shape: "shared",
    expected: [branch, leaf, leaf, leaf, leaf],
  },
  {
    type: "quadTree",
    shape: "shared-branch",
    expected: [
      branch,
      branch,
      leaf,
      leaf,
      leaf,
      leaf,
      branch,
      leaf,
      leaf,
      leaf,
      leaf,
      leaf,
      leaf,
    ],
  },
  { type: "quadTree", shape: "leaf", expected: [leaf] },
];

function specification(type, expected = []) {
  return {
    kind: "function",
    entry: type === "nextTree" ? "linkRows" : "buildTiles",
    paramNames: [type === "nextTree" ? "root" : "grid"],
    paramTypes: [type === "nextTree" ? "Node" : "integer[][]"],
    ...(type === "nextTree" ? { argTypes: ["nextTree"] } : {}),
    returnType: "Node",
    outputType: type,
    checker: "exact",
    cases: [
      {
        label: "returned structure",
        input: [type === "nextTree" ? [7] : [[1]]],
        expected,
      },
      { label: "empty structure after it", input: [[]], expected: [] },
    ],
  };
}

// Equivalent object graphs exercise each language's actual serializer. Equal
// values distinguish an identity check from a value check; shared branches
// distinguish active ancestors from every node encountered during the walk.
function candidate(language, type, shape) {
  const next = type === "nextTree";
  const actions = next
    ? {
        self: ["root.left = root"],
        ancestor: ["root.left = a", "a.right = root"],
        shared: ["root.left = a", "root.right = b", "a.next = b", "a.left = b"],
        equal: ["root.left = a", "root.right = b", "a.next = b"],
      }[shape]
    : {
        self: ["root.topLeft = root"],
        ancestor: ["root.topLeft = a", "a.bottomRight = root"],
        equal: [
          "root.topLeft = a",
          "root.topRight = b",
          "root.bottomLeft = c",
          "root.bottomRight = d",
        ],
        shared: [
          "root.topLeft = a",
          "root.topRight = a",
          "root.bottomLeft = a",
          "root.bottomRight = a",
        ],
        "shared-branch": [
          "root.topLeft = b",
          "root.topRight = b",
          "root.bottomLeft = a",
          "root.bottomRight = a",
          "b.topLeft = a",
          "b.topRight = a",
          "b.bottomLeft = a",
          "b.bottomRight = a",
        ],
        leaf: ["root.isLeaf = true", "root.topLeft = root"],
      }[shape];
  const isLeaf = (name) =>
    !next &&
    (shape === "ancestor"
      ? name !== "a"
      : shape === "shared-branch"
        ? name !== "b"
        : true);
  const names = ["a", "b", "c", "d"];
  const entry = next ? "linkRows" : "buildTiles";
  if (language === "javascript") {
    const make = (name) =>
      `const ${name} = new Node(${next ? 7 : "true"}${next ? "" : `, ${isLeaf(name)}`});`;
    return `function ${entry}(${next ? "root" : "grid"}) {
      if (${next ? "!root" : "!grid.length"}) return null;
      ${next ? "" : "const root = new Node(true, false);"}
      ${names.map(make).join("\n")}
      ${actions.map((action) => `${action};`).join("\n")}
      return root;
    }`;
  }
  if (language === "python") {
    const make = (name) =>
      `    ${name} = Node(${next ? 7 : "True"}${next ? "" : `, ${isLeaf(name) ? "True" : "False"}`})`;
    return `def ${entry}(${next ? "root" : "grid"}):
    if ${next ? "root is None" : "not grid"}:
        return None
${next ? "" : "    root = Node(True, False)\n"}${names.map(make).join("\n")}
${actions.map((action) => `    ${action.replaceAll("true", "True")}`).join("\n")}
    return root
`;
  }
  if (language === "cpp" || language === "java") {
    const cpp = language === "cpp";
    const declaration = cpp ? "auto*" : "Node";
    const pointer = cpp ? "Node*" : "Node";
    const make = (name) =>
      `${declaration} ${name} = new Node(${next ? 7 : 1}${next ? "" : `, ${isLeaf(name)}`});`;
    return `class Solution { public${cpp ? ":" : ""} ${pointer} ${entry}(${next ? `${pointer} root` : cpp ? "vector<vector<int>>& grid" : "int[][] grid"}) {
      if (${next ? (cpp ? "!root" : "root == null") : cpp ? "grid.empty()" : "grid.length == 0"}) return ${cpp ? "nullptr" : "null"};
      ${next ? "" : `${declaration} root = new Node(1, false);`}
      ${names.map(make).join("\n")}
      ${actions.map((action) => `${cpp ? action.replaceAll(".", "->") : action};`).join("\n")}
      return root;
    }};`;
  }
  const make = (name) =>
    `struct Node* ${name} = calloc(1, sizeof(*${name})); ${name}->val = 1; ${name}->isLeaf = ${isLeaf(name)};`;
  return `struct Node* buildTiles(int** grid, int gridSize, int* gridColSize) {
    if (!gridSize) return NULL;
    struct Node* root = calloc(1, sizeof(*root)); root->val = 1;
    ${names.map(make).join("\n")}
    ${actions.map((action) => `${action.replaceAll(".", "->")};`).join("\n")}
    return root;
  }`;
}

function check(results, fixture) {
  assert.equal(results.length, 2, "serialization must retain both cases");
  if (fixture.error)
    assert.match(
      results[0].error ?? "",
      new RegExp(fixture.error.replaceAll(".", "\\.")),
    );
  else {
    assert.equal(results[0].error, undefined);
    assert.deepEqual(results[0].actual, fixture.expected);
  }
  assert.equal(results[1].error, undefined);
  assert.deepEqual(results[1].actual, []);
}

let browser, server, base;
before(async () => {
  browser = await launchChromium();
  if (!browser) return;
  ({ server, base } = await startStaticServer({
    handle: (_request, response, url) => {
      const match = url.pathname.match(
        /^\/judges\/(nextTree|quadTree)-(.+)-cycles.json$/,
      );
      const fixture =
        match &&
        fixtures.find(
          ({ type, shape }) => type === match[1] && shape === match[2],
        );
      if (!fixture) return false;
      response.setHeader("content-type", "application/json");
      response.end(
        JSON.stringify(specification(fixture.type, fixture.expected)),
      );
      return true;
    },
  }));
});
after(async () => {
  await browser?.close();
  if (server) await new Promise((resolve) => server.close(resolve));
});

for (const language of ["javascript", "python"]) {
  for (const fixture of fixtures) {
    test(`${language} ${fixture.type} ${fixture.shape}`, async (t) => {
      if (!browser) return t.skip("playwright chromium unavailable");
      const page = await browser.newPage();
      try {
        await page.goto(`${base}/observer.html`);
        const summary = await page.evaluate(
          async ({ language, type, shape, code }) => {
            const { runBrowserTests } = await import("/runners.js");
            return runBrowserTests(`${type}-${shape}-cycles`, code, language);
          },
          {
            language,
            type: fixture.type,
            shape: fixture.shape,
            code: candidate(language, fixture.type, fixture.shape),
          },
        );
        assert.equal(summary.setupError, undefined);
        assert.equal(summary.total, 2);
        assert.equal(summary.cases.length, 2);
        assert.equal(summary.passed, fixture.error ? 1 : 2);
        if (fixture.error)
          assert.ok(summary.cases[0].error.includes(fixture.error));
        assert.equal(summary.cases[1].pass, true);
      } finally {
        await page.close();
      }
    });
  }
}

const run = promisify(execFile);
describe("specialized tree toolchain harnesses", { concurrency: 4 }, () => {
  for (const [language, compiler, flag] of [
    ["c", "cc", "-std=c17"],
    ["cpp", "g++", "-std=c++20"],
    ["java", "javac", null],
  ]) {
    let available = true;
    try {
      execFileSync(compiler, [language === "java" ? "-version" : "--version"], {
        stdio: "ignore",
      });
      if (language === "java")
        execFileSync("java", ["-version"], { stdio: "ignore" });
    } catch {
      available = false;
    }
    for (const fixture of fixtures.filter(
      (fixture) => language !== "c" || fixture.type === "quadTree",
    )) {
      test(
        `${language} ${fixture.type} ${fixture.shape}`,
        { skip: available ? false : `${compiler} is not available` },
        async () => {
          const dir = mkdtempSync(
            join(tmpdir(), "codetrial-specialized-tree-"),
          );
          try {
            const source = join(
              dir,
              language === "java" ? "Main.java" : `candidate.${language}`,
            );
            const binary = join(dir, "candidate");
            writeFileSync(
              source,
              generateHarness(
                language,
                specification(fixture.type, fixture.expected),
                candidate(language, fixture.type, fixture.shape),
              ),
            );
            if (language === "java") await run(compiler, [source]);
            else await run(compiler, [flag, source, "-o", binary]);
            const options = {
              encoding: "utf8",
              timeout: 10000,
              maxBuffer: 1 << 24,
            };
            const { stdout } = await (language === "java"
              ? run("java", ["-Xmx64m", "-cp", dir, "Main"], options)
              : run(binary, [], options));
            check(parseCompilerResults(stdout).results, fixture);
          } finally {
            rmSync(dir, { recursive: true, force: true });
          }
        },
      );
    }
  }
});
