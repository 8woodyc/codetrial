import { test } from "node:test";
import assert from "node:assert/strict";
import { failFetchWith } from "./source.js";

test("a page-map timeout does not cancel another caller's lookup or its cache", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const response = Promise.withResolvers();
  let requests = 0;
  const restore = failFetchWith(() => {
    requests += 1;
    return response.promise;
  });
  try {
    const { loadPageMap, loadPageMapWithin } =
      await import("../../web/problem-data.js?independent-deadlines");
    const short = loadPageMapWithin(5);
    const long = loadPageMapWithin(50);
    const shared = loadPageMap();
    let longFinished = false;
    void long.then(() => {
      longFinished = true;
    });
    t.mock.timers.tick(5);
    assert.equal(await short, null);
    assert.equal(longFinished, false);
    const pages = { "two-sum": { page: "chargeback-pair-match" } };
    response.resolve({ ok: true, status: 200, json: async () => pages });
    assert.equal(await long, pages);
    assert.equal(await shared, pages);
    t.mock.timers.tick(50);
    assert.equal(await loadPageMapWithin(5), pages);
    assert.equal(requests, 1);
  } finally {
    restore();
  }
});
