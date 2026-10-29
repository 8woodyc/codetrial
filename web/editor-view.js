// What the interview's code editor paints behind its textarea, for a page that
// keeps its own textarea: syntax colours, a line-number gutter, the active
// line and the bracket pair at the caret.
//
// The textarea stays the source of truth and keeps native editing. Two layers
// sit behind it and only paint, and they follow its scroll. The interview page
// paints from interview.js, which is tied to its state; this takes the same
// pieces (the tokenizer, `highlight`, the indentation guides and the bracket
// matcher) and none of that state.

import { matchingBrackets } from "./editor.js";
import { highlight } from "./highlight.js";
import { indentGuides } from "./indent-guides.js";
import { parseSyntax, prepareLanguage } from "./syntax-parser.js";
import { tokenize } from "./tokenizer.js";

/// Paints `editor`'s buffer into the `.editor-highlight` and `.editor-lines`
/// layers of `stack`, in the language `language()` names. Returns `paint`, to
/// call after the buffer or the language changed without an input event, and
/// `refresh`, which paints at the next frame.
export function mountEditorView({ stack, editor, language }) {
  const layer = stack.querySelector(".editor-highlight");
  const lines = stack.querySelector(".editor-lines");
  let tokenCache = null;
  let painted = null;
  let lineCount = 0;
  let frame = null;
  const grammars = new Set();

  // Tokens of the buffer, parsed once per buffer and language. The grammar
  // loads on first use; until it has, the scanner classifies, and the paint
  // is repeated when the grammar arrives.
  function classify(code, name) {
    if (tokenCache?.code !== code || tokenCache?.language !== name) {
      tokenCache = {
        code,
        language: name,
        ...tokenize(code, name, parseSyntax),
      };
    }
    return tokenCache;
  }

  function load(name) {
    if (grammars.has(name)) return;
    grammars.add(name);
    prepareLanguage(name)
      .then(() => {
        tokenCache = null;
        painted = null;
        paint();
      })
      // The scanner's colours stay; a grammar is an improvement, not a need.
      .catch(() => {});
  }

  function syncScroll() {
    layer.scrollTop = editor.scrollTop;
    layer.scrollLeft = editor.scrollLeft;
    lines.scrollTop = editor.scrollTop;
  }

  function paint() {
    if (frame !== null) {
      cancelAnimationFrame(frame);
      frame = null;
    }
    const name = language();
    load(name);
    const code = editor.value;
    const focused = document.activeElement === editor;
    const caret = editor.selectionStart === editor.selectionEnd;
    if (focused && caret) {
      stack.style.setProperty(
        "--active-line",
        String(code.slice(0, editor.selectionStart).split("\n").length - 1),
      );
    } else {
      stack.style.removeProperty("--active-line");
    }
    const brackets =
      focused && caret
        ? matchingBrackets(
            code,
            editor.selectionStart,
            editor.selectionEnd,
            name,
            classify,
          )
        : null;
    if (
      painted?.code === code &&
      painted.language === name &&
      painted.brackets?.[0] === brackets?.[0] &&
      painted.brackets?.[1] === brackets?.[1]
    )
      return;
    painted = { code, language: name, brackets };
    const classified = classify(code, name);
    classified.guides ??= indentGuides(code, classified.tokens);
    layer.firstElementChild.innerHTML = highlight(
      code,
      name,
      brackets,
      classified.tokens,
      classified.guides,
    );
    const count = code.split("\n").length;
    if (count !== lineCount) {
      lineCount = count;
      lines.textContent = Array.from(
        { length: count },
        (_, index) => index + 1,
      ).join("\n");
    }
    // The layers were rebuilt, and a scroll can run before the paint: Enter at
    // the bottom of a long buffer scrolls the textarea while they still hold
    // the old text.
    syncScroll();
  }

  // Input and selectionchange can arrive in the same frame; the buffer is read
  // once, at paint time.
  function refresh() {
    if (frame !== null) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      paint();
    });
  }

  editor.addEventListener("input", refresh);
  editor.addEventListener("scroll", syncScroll);
  editor.addEventListener("focus", paint);
  editor.addEventListener("blur", paint);
  document.addEventListener("selectionchange", () => {
    if (document.activeElement === editor) refresh();
  });
  paint();
  return { paint, refresh };
}
