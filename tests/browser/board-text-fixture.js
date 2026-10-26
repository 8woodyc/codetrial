import assert from "node:assert/strict";

export async function exerciseTextPalmRejection(page, canvas) {
  await canvas.evaluate((node) => {
    // Synthetic pointers are not registered with the browser's capture system.
    node.setPointerCapture = () => {};
    node.hasPointerCapture = () => false;
  });
  const box = page.locator(".board-text-draft:not([hidden])");
  const send = (type, pointerId, pointerType, x, y) =>
    canvas.evaluate(
      (node, event) => {
        const rect = node.getBoundingClientRect();
        node.dispatchEvent(
          new PointerEvent(event.type, {
            bubbles: true,
            cancelable: true,
            isPrimary: true,
            button: 0,
            pointerId: event.pointerId,
            pointerType: event.pointerType,
            clientX:
              rect.left +
              node.clientLeft +
              (event.x * node.clientWidth) / node.width,
            clientY:
              rect.top +
              node.clientTop +
              (event.y * node.clientHeight) / node.height,
          }),
        );
      },
      { type, pointerId, pointerType, x, y },
    );
  try {
    for (const palmFirst of [true, false]) {
      if (palmFirst) await send("pointerdown", 71, "touch", 50, 60);
      await send("pointerdown", 72, "pen", 400, 300);
      if (!palmFirst) await send("pointerdown", 71, "touch", 50, 60);
      await send("pointermove", 71, "touch", 900, 700);
      await send("pointerup", 71, "touch", 900, 700);
      assert.equal(
        await box.evaluate((node) => node.classList.contains("is-creating")),
        true,
        "lifting the palm does not finish the pen's text draft",
      );
      await send("pointermove", 72, "pen", 650, 380);
      await send("pointerup", 72, "pen", 650, 380);
      const position = await canvas.evaluate((node) => {
        const draft = node.parentElement.querySelector(".board-text-draft");
        return {
          x:
            ((parseFloat(draft.style.left) -
              node.offsetLeft -
              node.clientLeft) *
              node.width) /
            node.clientWidth,
          y:
            ((parseFloat(draft.style.top) - node.offsetTop - node.clientTop) *
              node.height) /
            node.clientHeight,
        };
      });
      assert.ok(Math.abs(position.x - 400) <= 1, "draft starts at the pen's x");
      assert.ok(Math.abs(position.y - 300) <= 1, "draft starts at the pen's y");
      await box.getByRole("button", { name: "Cancel", exact: true }).click();
    }
    await send("pointerdown", 73, "pen", 100, 100);
    await send("pointercancel", 73, "pen", 100, 100);
    assert.equal(
      await box.count(),
      0,
      "cancelled pen creation leaves no draft",
    );
    await send("pointerdown", 74, "touch", 200, 200);
    await send("pointerup", 74, "touch", 200, 200);
    assert.equal(
      await box.count(),
      1,
      "a finger can create after the pen is cancelled",
    );
    await box.getByRole("button", { name: "Cancel", exact: true }).click();
  } finally {
    await canvas.evaluate((node) => {
      delete node.setPointerCapture;
      delete node.hasPointerCapture;
    });
  }
}

// Compare actual DOM glyph pixels with the committed canvas, not two renders
// that could agree because they share the same incorrect baseline.
export async function exerciseTextBaseline(page, canvas, textTool) {
  await textTool.click();
  for (const { fontSize, text } of [32, 128].flatMap((fontSize) =>
    ["Hg\nHg", "a\tb"].map((text) => ({ fontSize, text })),
  )) {
    await canvas.scrollIntoViewIfNeeded();
    const rect = await canvas.boundingBox();
    const scale = await canvas.evaluate(
      (node) => node.clientWidth / node.width,
    );
    await page.mouse.move(rect.x + 30, rect.y + 30);
    await page.mouse.down();
    await page.mouse.move(
      rect.x + 30 + 320 * scale,
      rect.y + 30 + fontSize * 1.25 * scale,
    );
    await page.mouse.up();
    const box = page.locator(".board-text-draft:not([hidden])");
    const input = box.getByRole("textbox", { name: "Label text" });
    await input.focus();
    // Insert the whole label as pasted text, without using Tab to move focus.
    await page.keyboard.insertText(text);
    assert.equal(await input.inputValue(), text.replace(/\t/g, "    "));
    assert.equal(
      await input.evaluate((node) => node.selectionStart),
      text.replace(/\t/g, "    ").length,
      "expanding tabs keeps the caret after the inserted text",
    );
    assert.equal(
      await input.evaluate((node) => node.scrollWidth <= node.clientWidth),
      true,
      "the draft fits the expanded text without horizontal clipping",
    );
    await box.getByRole("button", { name: "Red text" }).click();
    await input.evaluate((node) => {
      node.style.caretColor = "transparent";
    });
    await input.scrollIntoViewIfNeeded();
    const clip = await input.boundingBox();
    const beforeCanvas = await canvas.boundingBox();
    const draft = await page.screenshot({ clip });
    await input.press("Control+Enter");
    const afterCanvas = await canvas.boundingBox();
    const committed = await page.screenshot({
      clip: {
        ...clip,
        x: clip.x + afterCanvas.x - beforeCanvas.x,
        y: clip.y + afterCanvas.y - beforeCanvas.y,
      },
    });
    const bounds = await page.evaluate(
      async (images) => {
        const result = [];
        for (const bytes of images) {
          const image = await createImageBitmap(
            new Blob([new Uint8Array(bytes)], { type: "image/png" }),
          );
          const surface = document.createElement("canvas");
          surface.width = image.width;
          surface.height = image.height;
          const context = surface.getContext("2d");
          context.drawImage(image, 0, 0);
          const { data } = context.getImageData(
            0,
            0,
            image.width,
            image.height,
          );
          const rows = [];
          let left = image.width;
          let right = -1;
          for (let y = 0; y < image.height; y++) {
            let painted = false;
            for (let x = 0; x < image.width; x++) {
              const at = (y * image.width + x) * 4;
              if (
                data[at] > data[at + 1] + 50 &&
                data[at] > data[at + 2] + 50
              ) {
                painted = true;
                left = Math.min(left, x);
                right = Math.max(right, x);
              }
            }
            if (painted) rows.push(y);
          }
          result.push({
            first: rows[0],
            last: rows.at(-1),
            count: rows.length,
            left,
            right,
          });
          image.close();
        }
        return result;
      },
      [Array.from(draft), Array.from(committed)],
    );
    assert.ok(
      bounds.every((bound) => bound.count > 0),
      "both images contain label glyphs",
    );
    const tolerance = 2 * (await page.evaluate(() => devicePixelRatio));
    for (const edge of ["left", "right"])
      assert.ok(
        Math.abs(bounds[0][edge] - bounds[1][edge]) <= tolerance,
        `${edge} glyph column for ${JSON.stringify(text)} at ${fontSize}px: draft=${bounds[0][edge]}, committed=${bounds[1][edge]}, tolerance=${tolerance}`,
      );
    assert.ok(
      Math.abs(bounds[0].first - bounds[1].first) <= tolerance,
      `first glyph row at ${fontSize}px: draft=${bounds[0].first}, committed=${bounds[1].first}, tolerance=${tolerance}`,
    );
    assert.ok(
      Math.abs(bounds[0].last - bounds[1].last) <= tolerance,
      `last glyph row at ${fontSize}px: draft=${bounds[0].last}, committed=${bounds[1].last}, tolerance=${tolerance}`,
    );
    await canvas.evaluate((node) =>
      node
        .closest("#board-panel, #example-board")
        .querySelector("#board-clear, [data-action=clear]")
        .click(),
    );
  }
}

// Run the same input contract through both production drawing surfaces.
export async function exerciseTextDraft(page, canvas, textTool) {
  await textTool.click();
  await canvas.scrollIntoViewIfNeeded();
  const rect = await canvas.boundingBox();
  const origin = { x: rect.x + 40, y: rect.y + 40 };
  const blank = await canvas.evaluate((node) => node.toDataURL());
  await page.mouse.move(origin.x, origin.y);
  await page.mouse.down();
  await page.mouse.move(origin.x + 6, origin.y + 6);
  const box = page.locator(".board-text-draft:not([hidden])");
  assert.ok(
    (await box.boundingBox()).width < 20,
    "creation starts at the pointer, without a preset minimum",
  );
  await page.mouse.move(origin.x + 120, origin.y + 36);
  await page.mouse.up();
  const input = box.getByRole("textbox", { name: "Label text" });
  const surface = page.locator(
    (await canvas.getAttribute("id")) === "board"
      ? "#board-panel"
      : "#example-board",
  );
  const penColor = surface
    .locator("#board-pens .board-color, [data-colors] .board-color")
    .first();
  const eraser = surface.locator('#board-eraser, [data-tool="eraser"]');
  assert.equal(await canvas.evaluate((node) => node.tabIndex), -1);
  assert.equal(
    await input.evaluate((node) => node === document.activeElement),
    true,
  );
  assert.equal(
    await box
      .getByRole("button", { name: "Black text" })
      .getAttribute("aria-pressed"),
    "true",
  );
  const size = () =>
    box.evaluate((node) => ({
      width: parseFloat(node.style.width),
      height: parseFloat(node.style.height),
    }));
  const original = await size();
  const font = await input.evaluate((node) => {
    const canvas = node
      .closest(".board-text-draft")
      .parentElement.querySelector("canvas");
    return (
      (parseFloat(node.style.fontSize) * canvas.clientWidth) / canvas.width
    );
  });
  assert.ok(
    Math.abs(original.height - font * 1.25) < 1,
    "one line fills the dragged height",
  );
  await input.fill("head\ntail");
  assert.ok((await size()).height > original.height + 10);
  await input.fill("head");
  assert.ok(Math.abs((await size()).height - original.height) < 1);
  await input.fill("wide label ".repeat(10));
  assert.ok((await size()).width > original.width + 20);
  await input.fill("head");
  assert.ok(Math.abs((await size()).width - original.width) < 1);
  const corner = await box.locator('[data-handle="se"]').boundingBox();
  await page.mouse.move(
    corner.x + corner.width / 2,
    corner.y + corner.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(
    corner.x + corner.width / 2 + 60,
    corner.y + corner.height / 2,
  );
  await page.mouse.up();
  assert.ok((await size()).width > original.width + 50);
  assert.ok(
    Math.abs((await size()).height - original.height) < 1,
    "corner resizing does not lock the aspect ratio",
  );
  await input.fill("WWWWWWWW");
  const beforeScale = await size();
  async function scaleHeight(delta) {
    await box.locator('[data-handle="s"]').scrollIntoViewIfNeeded();
    const handle = await box.locator('[data-handle="s"]').boundingBox();
    const x = handle.x + handle.width / 2;
    const y = handle.y + handle.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x, y + delta);
    await page.mouse.up();
    assert.equal(
      await input.evaluate(
        (node) =>
          node.scrollWidth <= node.clientWidth + 2 &&
          node.scrollHeight <= node.clientHeight + 2,
      ),
      true,
      "resizing keeps all text visible without another input event",
    );
  }
  await scaleHeight(50);
  assert.ok((await size()).width > beforeScale.width);
  await input.fill("one\ntwo\nthree");
  const expandedHeight = (await size()).height;
  await scaleHeight(-(await size()).height + 2);
  assert.ok(
    (await size()).height < expandedHeight,
    "the shrink gesture changes the draft height",
  );
  const coordinates = () =>
    box.evaluate((node) => {
      const canvas = node.parentElement.querySelector("canvas");
      return {
        x:
          (parseFloat(node.style.left) -
            canvas.offsetLeft -
            canvas.clientLeft) /
          canvas.clientWidth,
        y:
          (parseFloat(node.style.top) - canvas.offsetTop - canvas.clientTop) /
          canvas.clientHeight,
        width: parseFloat(node.style.width) / canvas.clientWidth,
        height: parseFloat(node.style.height) / canvas.clientHeight,
      };
    });
  const beforeResize = await coordinates();
  const viewport = page.viewportSize();
  await page.setViewportSize({
    width: viewport.width - 160,
    height: viewport.height,
  });
  await page.evaluate(async () => {
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
  });
  const afterResize = await coordinates();
  for (const key of Object.keys(beforeResize))
    assert.ok(
      Math.abs(beforeResize[key] - afterResize[key]) < 0.001,
      `viewport resize preserves logical ${key}`,
    );
  await page.setViewportSize(viewport);
  const draftValue = await input.inputValue();
  await penColor.click();
  assert.equal(await box.isVisible(), true, "a pen color keeps the draft open");
  assert.equal(await input.inputValue(), draftValue);
  await eraser.click();
  assert.equal(await box.isVisible(), true, "the eraser keeps the draft open");
  assert.equal(await input.inputValue(), draftValue);
  await box.getByRole("button", { name: "Red text" }).click();
  assert.equal(
    await box
      .getByRole("button", { name: "Red text" })
      .getAttribute("aria-pressed"),
    "true",
    "draft colors still work after changing tools",
  );
  await input.focus();
  await input.press("Escape");
  assert.equal(await canvas.evaluate((node) => node.toDataURL()), blank);

  await textTool.click();
  await canvas.click({ position: { x: 40, y: 40 } });
  assert.equal(
    await box
      .getByRole("button", { name: "Red text" })
      .getAttribute("aria-pressed"),
    "true",
    "the last text color survives cancellation",
  );
  await input.fill("  \n ");
  await input.press("Control+Enter");
  assert.equal(await canvas.evaluate((node) => node.toDataURL()), blank);

  await canvas.click({ position: { x: 40, y: 40 } });
  const error = box.getByRole("status");
  const emptyStatus = await error.evaluate((node) => {
    const style = getComputedStyle(node);
    return {
      display: style.display,
      visibility: style.visibility,
      height: node.getBoundingClientRect().height,
    };
  });
  assert.notEqual(emptyStatus.display, "none");
  assert.equal(emptyStatus.visibility, "visible");
  assert.equal(emptyStatus.height, 0);
  await input.fill(Array(21).fill("x").join("\n"));
  await input.press("Control+Enter");
  assert.match(
    await error.textContent(),
    /Text exceeds the board or input limits/,
  );
  assert.equal(await input.getAttribute("aria-invalid"), "true");
  assert.equal(
    await input.evaluate((node) => node === document.activeElement),
    true,
  );
  assert.equal(await canvas.evaluate((node) => node.toDataURL()), blank);
  await input.fill("head");
  assert.equal(await error.textContent(), "");
  await input.press("End");
  await input.press("Enter");
  await input.press("t");
  assert.equal(await input.inputValue(), "head\nt");
  await input.fill("head\ntail");
  assert.equal(
    await box
      .locator(".board-text-move, .board-text-resize")
      .evaluateAll((nodes) => nodes.every((node) => node.tabIndex === -1)),
    true,
  );
  await input.dispatchEvent("keydown", {
    key: "Enter",
    ctrlKey: true,
    isComposing: true,
  });
  assert.equal(
    await box.isVisible(),
    true,
    "IME confirmation must not commit a label",
  );
  await penColor.click();
  await input.press("Control+Enter");
  assert.equal(await box.count(), 0);
  assert.notEqual(await canvas.evaluate((node) => node.toDataURL()), blank);
  assert.equal(
    await canvas.evaluate((node) => node === document.activeElement),
    true,
  );
  await textTool.click();
  await exerciseTextPalmRejection(page, canvas);
  return blank;
}
