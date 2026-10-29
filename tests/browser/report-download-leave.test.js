import { after, before, test } from "node:test";
import assert from "node:assert/strict";

import {
  closeSetupCheck,
  DEFAULT_RUNTIME_CONFIG,
  launchChromium,
  startStaticServer,
} from "./source.js";

let browser = null;
let server = null;
let base = "";

before(async () => {
  browser = await launchChromium();
  if (!browser) return;
  ({ server, base } = await startStaticServer({
    runtimeConfig: DEFAULT_RUNTIME_CONFIG,
  }));
});

after(async () => {
  await browser?.close();
  await new Promise((closed) => (server ? server.close(closed) : closed()));
});

for (const saveOrder of [
  "before download",
  "during download",
  "after download",
]) {
  test(`Done waits for export when saving finishes ${saveOrder}`, async (t) => {
    if (!browser) return t.skip("playwright chromium unavailable");
    const page = await browser.newPage({ acceptDownloads: true });
    const saved = Promise.withResolvers();
    const mapped = Promise.withResolvers();
    const downloads = [];
    page.on("download", (download) => downloads.push(download));
    page.on("dialog", (dialog) => dialog.accept());
    await page.route("**/api/session", (route) =>
      route.fulfill({ json: { signedIn: true } }),
    );
    await page.route("**/api/reports", async (route) => {
      if (route.request().method() === "POST") await saved.promise;
      await route.fulfill({ json: { reports: [] } });
    });
    await page.route("**/problem-pages.json", async (route) => {
      await mapped.promise;
      await route.continue();
    });
    try {
      if (saveOrder === "before download") saved.resolve();
      await page.goto(`${base}/interview.html?problem=chargeback-pair-match`, {
        waitUntil: "domcontentloaded",
      });
      await page.waitForFunction(
        () =>
          document.querySelector("#candidate-case-input").placeholder !== "",
      );
      await closeSetupCheck(page);
      const saveRequested = page.waitForRequest(
        (request) =>
          request.url().endsWith("/api/reports") && request.method() === "POST",
      );
      await page.locator("#end").click();
      await saveRequested;
      const done = page.locator("#report-modal #done");
      const downloadButton = page.locator("#report-modal #download-report");
      await done.waitFor();
      const waitForSave = () =>
        page.waitForFunction(
          () =>
            document.querySelector("#report-save-status").textContent ===
            "Saved to your account",
        );
      if (saveOrder === "before download") {
        await waitForSave();
        assert.equal(await done.isEnabled(), true);
      }
      const downloading = page.waitForEvent("download");
      await downloadButton.click();
      assert.equal(await done.isDisabled(), true);
      assert.equal(await downloadButton.isDisabled(), true);
      if (saveOrder === "during download") {
        saved.resolve();
        await waitForSave();
        assert.equal(await done.isDisabled(), true);
      }
      // Exercise the leave handler too, even if a synthetic click bypasses
      // the disabled button. Navigation here would cancel the held export.
      await done.dispatchEvent("click");
      mapped.resolve();
      const download = await downloading;
      const chunks = [];
      for await (const chunk of await download.createReadStream())
        chunks.push(chunk);
      const markdown = Buffer.concat(chunks).toString("utf8");
      assert.match(markdown, /^# Interview Report/);
      assert.ok(
        markdown.includes(
          String.raw`Original problem: [1\. Two Sum](https://leetcode.com/problems/two-sum/)`,
        ),
      );
      assert.equal(await download.failure(), null);
      assert.equal(downloads.length, 1);
      await page.waitForFunction(
        () => !document.querySelector("#download-report").disabled,
      );
      if (saveOrder === "after download") {
        assert.equal(await done.isDisabled(), true);
        saved.resolve();
        await waitForSave();
      }
      assert.equal(await done.isEnabled(), true);
      await done.click();
      await page.waitForURL(`${base}/`);
    } finally {
      saved.resolve();
      mapped.resolve();
      await page.close();
    }
  });
}
