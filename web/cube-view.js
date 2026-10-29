// A cube a learner can turn, for a task page that asks about one: a 3D view
// to drag round, the same cube unfolded flat, buttons and a notation box to
// turn it, and the position code the task's solver reads, with what each of
// its digits says.
//
// The model, the notation and the code are `cube.js`; this only draws them.
// The views, one state drawn several ways, follow `cubst`, a Typst package by
// Anson Lai (MIT); see `cube.js` for what was taken from it.

import {
  COLORS,
  FACES,
  MAX_MOVES,
  SCHEME,
  apply,
  formatMove,
  netCells,
  parseAlgorithm,
  positionCode,
  readPlaces,
  solved,
  stateFromCode,
  turn,
} from "./cube.js";

/// One cubie's edge, in pixels. The cube is twice this across.
const CUBIE = 72;
/// How long a layer takes to turn, and the most moves a notation box animates
/// one by one; a longer algorithm is applied at once.
const TURN_MS = 220;
const MAX_ANIMATED = 8;

/// How to turn a square that faces the viewer to face `normal`, with y up as
/// in the model; the pivot's own y runs down, which these rotations account for.
const FACING = new Map([
  ["0,0,1", ""],
  ["0,0,-1", "rotateY(180deg)"],
  ["1,0,0", "rotateY(90deg)"],
  ["-1,0,0", "rotateY(-90deg)"],
  ["0,1,0", "rotateX(90deg)"],
  ["0,-1,0", "rotateX(-90deg)"],
]);

const FACE_NAMES = {
  R: "right",
  L: "left",
  U: "up",
  D: "down",
  F: "front",
  B: "back",
};

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

function button(label, name, onclick) {
  const node = el("button", "", label);
  node.type = "button";
  node.setAttribute("aria-label", name);
  node.onclick = onclick;
  return node;
}

/// A square of the cube in 3D, `inset` pixels out along `normal` from the
/// centre of its cubie.
function square(normal, colour, label) {
  const node = el("div", colour ? "cube-sticker" : "cube-body");
  const facing = FACING.get(normal.join());
  node.style.transform = `${facing} translateZ(${CUBIE / 2 + (colour ? 1 : 0)}px)`;
  if (colour) {
    node.style.background = COLORS[colour];
    if (label) node.textContent = label;
  }
  return node;
}

/// The cubies of `state` as 3D elements. `numbers` writes each cubie's digit
/// on its up or down sticker; `moving` picks the ones a layer turn carries.
function cubies(state, numbers, moving) {
  const layer = [];
  const rest = [];
  for (const piece of state) {
    const [x, y, z] = piece.position;
    const node = el("div", "cube-cubie");
    node.style.transform = `translate3d(${(x * CUBIE) / 2}px, ${(-y * CUBIE) / 2}px, ${(z * CUBIE) / 2}px)`;
    // A black body, so a layer turning shows no gap, and the stickers on it.
    for (const normal of Object.values(FACES))
      node.append(square(normal, null));
    for (const { face, normal } of piece.stickers) {
      const label =
        numbers && "UD".includes(face)
          ? piece.id === 7
            ? "·"
            : String(piece.id + 1)
          : "";
      node.append(square(normal, SCHEME[face], label));
    }
    (moving?.(piece) ? layer : rest).push(node);
  }
  return { layer, rest };
}

/// The unfolded cube as an SVG: up on top, then left, front, right and back in
/// a row, and down below.
function drawNet(svg, state, numbers) {
  const size = 26;
  const gap = 14;
  const origin = {
    U: [1, 0],
    L: [0, 1],
    F: [1, 1],
    R: [2, 1],
    B: [3, 1],
    D: [1, 2],
  };
  const NS = "http://www.w3.org/2000/svg";
  const make = (tag, attributes) => {
    const node = document.createElementNS(NS, tag);
    for (const [name, value] of Object.entries(attributes))
      node.setAttribute(name, value);
    return node;
  };
  const span = 2 * size + gap;
  svg.setAttribute("viewBox", `0 -10 ${4 * span} ${3 * span - gap + 10}`);
  svg.replaceChildren();
  const faces = netCells(state);
  for (const [letter, [column, row]] of Object.entries(origin)) {
    faces[letter].forEach((cells, r) =>
      cells.forEach(({ colour, cubie, face }, c) => {
        const x = column * span + c * size;
        const y = row * span + r * size;
        svg.append(
          make("rect", {
            x,
            y,
            width: size,
            height: size,
            rx: 3,
            fill: COLORS[colour],
            stroke: "#11110f",
            "stroke-width": 2,
          }),
        );
        if (numbers && "UD".includes(face)) {
          const text = make("text", {
            x: x + size / 2,
            y: y + size / 2 + 4,
            "text-anchor": "middle",
            "font-size": 12,
            "font-weight": 700,
            fill: "#11110f",
          });
          text.textContent = cubie === 8 ? "·" : String(cubie);
          svg.append(text);
        }
      }),
    );
    const label = make("text", {
      x: column * span + size,
      y: row * span - 2,
      "text-anchor": "middle",
      "font-size": 9,
      fill: "#b7b2a5",
    });
    label.textContent = letter;
    if (row > 0 || letter === "U") svg.append(label);
  }
}

/// Fills `root`, a `<details>`, with the cube and its controls, and reveals it.
export function mountCubeView(root) {
  const body = el("div", "cube-body-panel");
  root.append(body);
  root.hidden = false;

  let state = solved();
  let history = [];
  let numbers = false;
  let view = "3d";
  let busy = Promise.resolve();
  // The view a learner has dragged to, in degrees; up, left and front show.
  let tilt = { x: -26, y: -38 };

  // Views.
  const views = el("div", "cube-views");
  views.setAttribute("role", "group");
  views.setAttribute("aria-label", "Cube view");
  const stage = el("div", "cube-stage");
  const scene = el("div", "cube-scene");
  scene.tabIndex = 0;
  scene.setAttribute("role", "img");
  scene.setAttribute(
    "aria-label",
    "A 2x2x2 cube; drag to look around, or use the arrow keys",
  );
  const pivot = el("div", "cube-pivot");
  scene.append(pivot);
  const flat = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  flat.setAttribute("class", "cube-net");
  flat.setAttribute("role", "img");
  flat.setAttribute("aria-label", "The cube unfolded, seen from outside");
  stage.append(scene, flat);
  const viewButtons = new Map();
  for (const [id, label] of [
    ["3d", "3D cube"],
    ["net", "Unfolded"],
  ]) {
    const node = button(label, label, () => {
      view = id;
      paint();
    });
    viewButtons.set(id, node);
    views.append(node);
  }
  const digits = el("label", "cube-option");
  const digitsBox = document.createElement("input");
  digitsBox.type = "checkbox";
  digitsBox.onchange = () => {
    numbers = digitsBox.checked;
    paint();
  };
  digits.append(digitsBox, " Show each cubie's number");
  views.append(digits);

  // Turning it.
  const moves = el("div", "cube-moves");
  moves.setAttribute("role", "group");
  moves.setAttribute("aria-label", "Turn a face");
  for (const face of Object.keys(FACE_NAMES))
    for (const [suffix, amount, how] of [
      ["", 1, "clockwise"],
      ["'", -1, "counterclockwise"],
      ["2", 2, "half turn"],
    ])
      moves.append(
        button(
          face + suffix,
          `${face + suffix}: ${FACE_NAMES[face]} face, ${how}`,
          () => run([{ face, amount }]),
        ),
      );

  const form = el("form", "cube-algorithm");
  const algorithm = document.createElement("input");
  algorithm.autocomplete = "off";
  algorithm.spellcheck = false;
  algorithm.placeholder = "R D R' D'";
  algorithm.setAttribute("aria-label", "Algorithm to apply");
  algorithm.setAttribute("aria-describedby", "cube-message");
  algorithm.maxLength = 200;
  const applyButton = button("Apply", "Apply the algorithm", () => {});
  applyButton.type = "submit";
  const reset = button("Reset", "Back to the solved cube", () => {
    state = solved();
    history = [];
    message("");
    paint();
  });
  form.append(algorithm, applyButton, reset);
  form.onsubmit = (event) => {
    event.preventDefault();
    try {
      const parsed = parseAlgorithm(algorithm.value);
      message("");
      run(parsed, parsed.length <= MAX_ANIMATED);
    } catch (error) {
      message(error.message);
    }
  };

  const shown = el("p", "cube-history");
  const status = el("p", "cube-status");
  status.id = "cube-message";
  status.setAttribute("role", "status");
  const message = (text) => {
    status.textContent = text;
  };

  // The position code and what its digits say.
  const readout = el("div", "cube-code");
  const code = el("output", "cube-code-value");
  code.setAttribute("aria-label", "Position code");
  const table = el("table", "cube-places");
  readout.append(code, table);

  const load = el("form", "cube-load");
  const loadInput = document.createElement("input");
  loadInput.autocomplete = "off";
  loadInput.spellcheck = false;
  loadInput.inputMode = "numeric";
  loadInput.maxLength = 14;
  loadInput.placeholder = "21653471111312";
  loadInput.setAttribute("aria-label", "Position code to show");
  loadInput.setAttribute("aria-describedby", "cube-message");
  load.append(
    loadInput,
    button("Show", "Show this position", () => {}),
  );
  load.lastElementChild.type = "submit";
  load.onsubmit = (event) => {
    event.preventDefault();
    try {
      state = stateFromCode(loadInput.value.trim());
      history = [];
      message("");
      paint();
    } catch (error) {
      message(error.message);
    }
  };

  const legend = el("details", "cube-legend");
  legend.append(
    el("summary", "", "How to read it"),
    el(
      "p",
      "",
      "A letter turns that face a quarter turn clockwise as you look at it: R right, L left, U up, D down, F front, B back. R' is counterclockwise and R2 a half turn. A 2x2x2 cube has no fixed centres, so the up-left-front corner (ULF) is held still: turning L, U or F is the same cube as turning R, D or B, held differently, and the task only turns R, B and D.",
    ),
    el(
      "p",
      "",
      "The position code lists, for the seven other corners' places in the order URF, DRF, DFL, URB, DRB, DBL, UBL, which cubie sits there (1 to 7, the place it has on a solved cube), then each cubie's twist plus one: 1 when its yellow or white sticker faces up or down, 2 or 3 when it is turned one way or the other.",
    ),
  );

  body.append(views, stage, moves, form, shown, status, readout, load, legend);

  // Drawing.
  function paint() {
    for (const [id, node] of viewButtons)
      node.setAttribute("aria-pressed", String(id === view));
    scene.hidden = view !== "3d";
    flat.style.display = view === "net" ? "" : "none";
    if (view === "3d") {
      pivot.style.transform = `rotateX(${tilt.x}deg) rotateY(${tilt.y}deg)`;
      pivot.replaceChildren(...cubies(state, numbers).rest);
    } else {
      drawNet(flat, state, numbers);
    }
    const current = positionCode(state);
    code.textContent = current;
    table.replaceChildren();
    const head = el("tr");
    const cubiesRow = el("tr");
    const twistsRow = el("tr");
    head.append(el("th", "", "Place"));
    cubiesRow.append(el("th", "", "Cubie"));
    twistsRow.append(el("th", "", "Twist + 1"));
    for (const { place, cubie, twist } of readPlaces(state)) {
      head.append(el("th", "", place));
      cubiesRow.append(el("td", "", String(cubie)));
      twistsRow.append(el("td", "", String(twist + 1)));
    }
    table.append(head, cubiesRow, twistsRow);
    shown.textContent = history.length
      ? `Moves so far: ${history.map(formatMove).join(" ")}`
      : "Solved, or a position you loaded.";
  }

  // Turning, with a layer that swings round when motion is welcome.
  const reduced = () =>
    globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  async function swing(move) {
    const axis = FACES[move.face];
    const { layer, rest } = cubies(
      state,
      numbers,
      (piece) =>
        axis[0] * piece.position[0] +
          axis[1] * piece.position[1] +
          axis[2] * piece.position[2] >
        0,
    );
    const group = el("div", "cube-layer");
    group.append(...layer);
    pivot.replaceChildren(...rest, group);
    // Painted at rest first, so the transition has somewhere to start.
    group.getBoundingClientRect();
    group.style.transition = `transform ${TURN_MS}ms ease`;
    group.style.transform = `rotate3d(${axis[0]}, ${-axis[1]}, ${axis[2]}, ${90 * move.amount}deg)`;
    await new Promise((resolve) => setTimeout(resolve, TURN_MS + 30));
  }

  function run(list, animate = true) {
    // One turn at a time, in the order asked.
    busy = busy.then(async () => {
      if (animate && view === "3d" && !reduced()) {
        for (const move of list) {
          await swing(move);
          state = turn(state, move.face, move.amount);
          history = [...history, move].slice(-MAX_MOVES);
          paint();
        }
        return;
      }
      state = apply(state, list);
      history = [...history, ...list].slice(-MAX_MOVES);
      paint();
    });
    return busy;
  }

  // Looking round: drag, or the arrow keys when the cube has focus.
  let drag = null;
  scene.addEventListener("pointerdown", (event) => {
    drag = { x: event.clientX, y: event.clientY, tilt: { ...tilt } };
    scene.setPointerCapture(event.pointerId);
  });
  scene.addEventListener("pointermove", (event) => {
    if (!drag) return;
    tilt = {
      x: Math.max(
        -89,
        Math.min(89, drag.tilt.x - (event.clientY - drag.y) / 2),
      ),
      y: drag.tilt.y + (event.clientX - drag.x) / 2,
    };
    pivot.style.transform = `rotateX(${tilt.x}deg) rotateY(${tilt.y}deg)`;
  });
  const release = () => {
    drag = null;
  };
  scene.addEventListener("pointerup", release);
  scene.addEventListener("pointercancel", release);
  scene.addEventListener("keydown", (event) => {
    const step = {
      ArrowLeft: [0, -15],
      ArrowRight: [0, 15],
      ArrowUp: [-15, 0],
      ArrowDown: [15, 0],
    }[event.key];
    if (!step) return;
    event.preventDefault();
    tilt = {
      x: Math.max(-89, Math.min(89, tilt.x + step[0])),
      y: tilt.y + step[1],
    };
    pivot.style.transform = `rotateX(${tilt.x}deg) rotateY(${tilt.y}deg)`;
  });

  paint();
  return { paint, run };
}
