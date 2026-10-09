import { test } from "node:test";
import assert from "node:assert/strict";

let captionCopies = 0;

async function setupCaptions(t) {
  // Caption state is module-level, so each case imports a fresh copy.
  const captions = await import(
    `../../web/captions.js?copy=${++captionCopies}`
  );
  const nodes = {
    captionsText: { textContent: "" },
    captionsBar: { hidden: true },
  };
  captions.initCaptions({ nodes });
  t.mock.timers.enable({ apis: ["setTimeout"] });
  return { captions, nodes };
}

test("the listening placeholder hides after twelve seconds", async (t) => {
  const { captions, nodes } = await setupCaptions(t);
  nodes.captionsText.textContent = "Listening...";
  captions.showCaptions();

  assert.equal(nodes.captionsBar.hidden, false);
  t.mock.timers.tick(11999);
  assert.equal(nodes.captionsBar.hidden, false);
  t.mock.timers.tick(1);
  assert.equal(nodes.captionsBar.hidden, true);
});

test("candidate captions keep their window and renew the idle timeout", async (t) => {
  const { captions, nodes } = await setupCaptions(t);
  const text =
    `${"I am checking the input constraints ".repeat(6)}` +
    "before choosing a data structure. I will use a hash map.";

  captions.updateCaptions("you", text, "you-1");
  assert.equal(nodes.captionsText.textContent, "[You]: I will use a hash map.");
  assert.equal(nodes.captionsBar.hidden, false);

  t.mock.timers.tick(11000);
  assert.equal(nodes.captionsBar.hidden, false);

  captions.updateCaptions("you", "I will scan the array once.", "you-1");
  assert.equal(
    nodes.captionsText.textContent,
    "[You]: I will scan the array once.",
  );

  // The previous update's deadline must not hide the renewed caption.
  t.mock.timers.tick(1000);
  assert.equal(nodes.captionsBar.hidden, false);
  t.mock.timers.tick(10999);
  assert.equal(nodes.captionsBar.hidden, false);
  t.mock.timers.tick(1);
  assert.equal(nodes.captionsBar.hidden, true);
});

test("interim interviewer captions pace cumulative updates", async (t) => {
  const { captions, nodes } = await setupCaptions(t);

  captions.updateCaptions("interviewer", "Use a hash map.", "jim-1");
  assert.equal(nodes.captionsText.textContent, "[Jim]: Use a ha");
  assert.equal(nodes.captionsBar.hidden, false);

  t.mock.timers.tick(300);
  assert.equal(nodes.captionsText.textContent, "[Jim]: Use a hash map.");

  captions.updateCaptions("interviewer", "Use a", "jim-1");
  assert.equal(nodes.captionsText.textContent, "[Jim]: Use a hash map.");

  captions.updateCaptions("interviewer", "Try sorting.", "jim-2");
  assert.equal(nodes.captionsText.textContent, "[Jim]: Try sort");
  t.mock.timers.tick(300);
  assert.equal(nodes.captionsText.textContent, "[Jim]: Try sorting.");
});
