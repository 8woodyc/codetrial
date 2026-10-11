/// Only cards leave the first panel; controls retain their reading order.
export function mountProblemGrid() {
  if (typeof window.matchMedia !== "function") return;
  const picker = document.querySelector("details.problem-picker");
  const grid = picker.querySelector(".problem-grid");
  const setup = document.querySelector(".setup-grid");
  const summary = picker.querySelector("summary");
  const home = document.createComment("problem-grid-home");
  grid.before(home);
  grid.id = "problem-cards";
  summary.setAttribute("aria-controls", grid.id);
  const columns = window.matchMedia("(min-width: 761px)");

  const lobby = setup.parentElement;
  const panel = setup.querySelector(".setup-panel");
  const outline = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  outline.classList.add("problem-picker-outline");
  outline.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(outline.namespaceURI, "path");
  outline.append(path);
  lobby.append(outline);

  // Measure the live layout, including changes to any setup panel's height.
  function drawOutline() {
    const connected = columns.matches && picker.open && !picker.hidden;
    lobby.classList.toggle("problem-picker-connected", connected);
    outline.toggleAttribute("hidden", !connected);
    if (!connected) return;
    const origin = lobby.getBoundingClientRect();
    const top = panel.getBoundingClientRect();
    const bottom = grid.getBoundingClientRect();
    const x = top.left - origin.left;
    const y = top.top - origin.top;
    const padding = parseFloat(getComputedStyle(grid).paddingRight);
    const entries = [
      ...grid.querySelectorAll(":scope > .problem-entry:not([hidden])"),
    ].map((entry) => entry.getBoundingClientRect());
    const right =
      Math.max(top.right, ...entries.map((entry) => entry.right + padding)) -
      origin.left;
    const edge = top.right - origin.left;
    const join = bottom.top - origin.top;
    const end = bottom.bottom - origin.top;
    const radius = parseFloat(getComputedStyle(panel).borderTopLeftRadius);
    path.setAttribute(
      "d",
      [
        `M ${x + radius} ${y}`,
        `H ${edge - radius} Q ${edge} ${y} ${edge} ${y + radius}`,
        ...(right - edge >= 2 * radius
          ? [
              `V ${join - radius} Q ${edge} ${join} ${edge + radius} ${join}`,
              `H ${right - radius} Q ${right} ${join} ${right} ${join + radius}`,
            ]
          : right > edge
            ? [`V ${join} H ${right}`]
            : []),
        `V ${end - radius} Q ${right} ${end} ${right - radius} ${end}`,
        `H ${x + radius} Q ${x} ${end} ${x} ${end - radius}`,
        `V ${y + radius} Q ${x} ${y} ${x + radius} ${y} Z`,
      ].join(" "),
    );
  }
  const cardObserver = new MutationObserver(drawOutline);
  cardObserver.observe(grid, {
    subtree: true,
    attributes: true,
    attributeFilter: ["hidden"],
  });
  const observer = new ResizeObserver(drawOutline);
  observer.observe(lobby);
  observer.observe(grid);
  for (const item of setup.querySelectorAll(".setup-panel"))
    observer.observe(item);

  grid.addEventListener("beforematch", () => {
    picker.open = true;
    update();
  });

  function update() {
    const focused = document.activeElement;
    const hadFocus = grid.contains(focused);
    if (columns.matches) {
      if (grid.previousElementSibling !== panel) panel.after(grid);
      // Detached desktop cards must inherit the picker's hidden state.
      if (picker.hidden) grid.hidden = true;
      else if (picker.open) grid.removeAttribute("hidden");
      else grid.setAttribute("hidden", "until-found");
    } else {
      if (grid.parentElement !== picker) home.after(grid);
      grid.hidden = picker.hidden;
    }
    drawOutline();
    if (hadFocus && !picker.hidden) {
      if (!picker.open) summary.focus({ preventScroll: true });
      else focused.focus({ preventScroll: true });
    }
  }

  // The toggle event is queued and may run after a paint. Observe open instead
  // so native controls, detached cards and their outline change before paint.
  const stateObserver = new MutationObserver(update);
  stateObserver.observe(picker, {
    attributes: true,
    attributeFilter: ["open", "hidden"],
  });
  columns.addEventListener("change", update);
  update();
}
