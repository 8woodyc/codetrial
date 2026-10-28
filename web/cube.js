// A 2x2x2 cube for the rubik task's page: its stickers and turns, the notation
// a learner may type, the flat nets it is drawn in, and the position code the
// task's solver reads.
//
// The ideas are those of `cubst`, a Typst package by Anson Lai (MIT): one
// geometric model that every view is drawn from, a sticker moving when its
// layer turns about an axis, the official sticker colours, one scanner for
// moves with groups, commutators and conjugates, and several views of one
// state. This is a rewrite for a browser and for one puzzle, not a port: there
// is no Typst here, and the model is a corner piece with three stickers, since
// a 2x2x2 has nothing else.
//
// Free of the DOM, so `tests/browser/cube.test.js` can drive it.

/// The face each letter names, as the unit vector pointing out of it. x is
/// right, y is up and z is toward the viewer, so a face is turned clockwise as
/// seen from outside by a quarter turn about the opposite handed axis.
export const FACES = Object.freeze({
  U: [0, 1, 0],
  D: [0, -1, 0],
  R: [1, 0, 0],
  L: [-1, 0, 0],
  F: [0, 0, 1],
  B: [0, 0, -1],
});

/// Yellow on top and green in front, the usual scheme, in the shades cubst
/// uses: white, yellow and orange stay apart from each other and from red.
export const SCHEME = Object.freeze({
  U: "yellow",
  D: "white",
  F: "green",
  B: "blue",
  R: "red",
  L: "orange",
});
export const COLORS = Object.freeze({
  white: "#ffffff",
  yellow: "#ffd500",
  orange: "#ff5800",
  red: "#b71234",
  green: "#009b48",
  blue: "#0046ad",
});

/// The corner held fixed to remove whole-cube rotations, and the seven places
/// the position code names, in the order it names them. A place's number is its
/// digit position in the code less one; a cubie's number is the place it
/// occupies on a solved cube.
export const FIXED = Object.freeze({ name: "ULF", at: [-1, 1, 1] });
export const PLACES = Object.freeze([
  { name: "URF", at: [1, 1, 1] },
  { name: "DRF", at: [1, -1, 1] },
  { name: "DFL", at: [-1, -1, 1] },
  { name: "URB", at: [1, 1, -1] },
  { name: "DRB", at: [1, -1, -1] },
  { name: "DBL", at: [-1, -1, -1] },
  { name: "UBL", at: [-1, 1, -1] },
]);

/// The most moves one algorithm may expand to, so `(R U)99999` is refused
/// instead of run.
export const MAX_MOVES = 200;

const FACE_OF = new Map(
  Object.entries(FACES).map(([letter, vector]) => [vector.join(), letter]),
);

const cross = (a, b) => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const same = (a, b) => a[0] === b[0] && a[1] === b[1] && a[2] === b[2];

/// One clockwise quarter turn, seen from outside along `axis`, of the vector
/// `v`: a right-handed turn of minus ninety degrees about the outward axis.
function quarter(axis, v) {
  const side = cross(axis, v);
  const along = dot(axis, v);
  return [
    -side[0] + axis[0] * along,
    -side[1] + axis[1] * along,
    -side[2] + axis[2] * along,
  ];
}

/// A corner: which cubie it is, where it sits, and its three stickers, each
/// the face it was solved on and the way it now points.
function corner(id, at) {
  return {
    id,
    position: [...at],
    stickers: [0, 1, 2].map((axis) => {
      const normal = [0, 0, 0];
      normal[axis] = at[axis];
      return { face: FACE_OF.get(normal.join()), normal };
    }),
  };
}

/// The solved cube: eight corners, in the order of their cubie numbers.
export function solved() {
  return [...PLACES, FIXED].map((place, id) => corner(id, place.at));
}

/// The state after `amount` quarter turns of `face`, clockwise as seen from
/// outside; negative turns are counterclockwise. The state is never changed.
export function turn(state, face, amount = 1) {
  const axis = FACES[face];
  const quarters = ((amount % 4) + 4) % 4;
  if (!axis) throw new Error(`Unknown face "${face}"`);
  return state.map((piece) => {
    if (dot(axis, piece.position) <= 0 || quarters === 0)
      return structuredClone(piece);
    let position = piece.position;
    let stickers = piece.stickers;
    for (let step = 0; step < quarters; step += 1) {
      position = quarter(axis, position);
      stickers = stickers.map(({ face: home, normal }) => ({
        face: home,
        normal: quarter(axis, normal),
      }));
    }
    return { id: piece.id, position, stickers };
  });
}

// --- Notation ---------------------------------------------------------------

const MOVE = /^([URFDLB])(2'|'2|2|'|’)?/;
const CLOSER = /^(\d*)(['’]?)/;

function suffixAmount(suffix) {
  if (!suffix) return 1;
  if (suffix === "2") return 2;
  if (suffix === "2'" || suffix === "'2") return -2;
  return -1;
}

const inverse = (moves) =>
  moves.map(({ face, amount }) => ({ face, amount: -amount })).reverse();

/// The punctuation and the moves of `text`, in order. A closing bracket carries
/// its repeat count and its inversion mark.
function tokenize(text) {
  const tokens = [];
  let rest = text;
  for (;;) {
    rest = rest.replace(/^\s+/, "");
    if (!rest) return tokens;
    const character = rest[0];
    if ("([,:".includes(character)) {
      tokens.push({ kind: character });
      rest = rest.slice(1);
    } else if (")]".includes(character)) {
      const [taken, times, inverted] = rest.slice(1).match(CLOSER);
      tokens.push({
        kind: character,
        times: times === "" ? 1 : Number(times),
        inverted: inverted !== "",
      });
      rest = rest.slice(1 + taken.length);
    } else {
      const match = rest.match(MOVE);
      if (!match) throw new Error(`Cannot read "${rest.slice(0, 8).trim()}"`);
      tokens.push({
        kind: "move",
        move: { face: match[1], amount: suffixAmount(match[2]) },
      });
      rest = rest.slice(match[0].length);
    }
  }
}

/// The moves from token `at` to a token whose kind is in `stop`, and the index
/// of that token. `( A )N` repeats and `( A )'` inverts; `[ A, B ]` is the
/// commutator A B A' B'; `[ A: B ]` and `A: B` are the conjugate A B A', the
/// colon binding to the end of the enclosing group.
function parse(tokens, at, stop) {
  const out = [];
  let index = at;
  while (index < tokens.length) {
    const token = tokens[index];
    if (stop.includes(token.kind)) break;
    if (token.kind === "move") {
      out.push(token.move);
      index += 1;
    } else if (token.kind === "(" || token.kind === "[") {
      const closer = token.kind === "(" ? ")" : "]";
      const [first, comma] = parse(
        tokens,
        index + 1,
        closer === "]" ? ["]", ","] : [")"],
      );
      let sequence = first;
      let end = comma;
      if (closer === "]" && tokens[comma]?.kind === ",") {
        const [second, last] = parse(tokens, comma + 1, ["]"]);
        sequence = [...first, ...second, ...inverse(first), ...inverse(second)];
        end = last;
      }
      if (tokens[end]?.kind !== closer)
        throw new Error(`A "${token.kind}" is not closed`);
      const { times, inverted } = tokens[end];
      if (inverted) sequence = inverse(sequence);
      if (out.length + sequence.length * times > MAX_MOVES)
        throw new Error(`More than ${MAX_MOVES} moves`);
      for (let repeat = 0; repeat < times; repeat += 1) out.push(...sequence);
      index = end + 1;
    } else if (token.kind === ":") {
      const conjugator = out.splice(0, out.length);
      const [rest, end] = parse(tokens, index + 1, stop);
      out.push(...conjugator, ...rest, ...inverse(conjugator));
      index = end;
    } else {
      throw new Error(`Unexpected "${token.kind}"`);
    }
  }
  return [out, index];
}

/// The moves `text` stands for: `{face, amount}` with an amount of 1, 2, -1 or
/// -2. Throws an Error that names what could not be read.
export function parseAlgorithm(text) {
  if (typeof text !== "string") throw new Error("Enter an algorithm");
  const [moves, end] = parse(tokenize(text), 0, []);
  if (end < tokenize(text).length) throw new Error("A bracket is not opened");
  if (moves.length > MAX_MOVES) throw new Error(`More than ${MAX_MOVES} moves`);
  return moves;
}

/// A move as it is written: `R`, `R'`, `R2`.
export function formatMove({ face, amount }) {
  const quarters = ((amount % 4) + 4) % 4;
  return face + (quarters === 1 ? "" : quarters === 3 ? "'" : "2");
}

/// The state after every move of `moves`.
export function apply(state, moves) {
  return moves.reduce(
    (current, move) => turn(current, move.face, move.amount),
    state,
  );
}

// --- The position code -------------------------------------------------------

/// How a corner is twisted: 0 when its up or down sticker points up or down,
/// and otherwise 1 or 2, counted the way the solver's tables count. Corners
/// come in two handednesses, so which of the sideways directions is 1 depends
/// on the sign of the corner's x times y times z.
function twist(piece) {
  const { normal } = piece.stickers.find(({ face }) => "UD".includes(face));
  if (normal[1] !== 0) return 0;
  const [x, y, z] = piece.position;
  const handed = x * y * z > 0;
  return (normal[2] !== 0) === handed ? 1 : 2;
}

/// The whole-cube turn that puts the fixed corner back at its place, the right
/// way round, as a matrix. A 2x2x2 has no centres, so which corner is "fixed"
/// is a choice, and this is what makes every turn of every face reach the same
/// code as the turn of the opposite face the other way.
function reorientation(state) {
  const home = corner(FIXED_ID, FIXED.at).stickers;
  const held = state.find(({ id }) => id === FIXED_ID).stickers;
  const matrix = [
    [0, 0, 0],
    [0, 0, 0],
    [0, 0, 0],
  ];
  for (const { face, normal } of held) {
    const target = home.find((sticker) => sticker.face === face).normal;
    for (let row = 0; row < 3; row += 1)
      for (let column = 0; column < 3; column += 1)
        matrix[row][column] += target[row] * normal[column];
  }
  return matrix;
}
const FIXED_ID = 7;

const through = (matrix, v) => matrix.map((row) => dot(row, v));

/// `state` turned as a whole so that its fixed corner is home.
export function normalized(state) {
  const matrix = reorientation(state);
  return state.map((piece) => ({
    id: piece.id,
    position: through(matrix, piece.position),
    stickers: piece.stickers.map(({ face, normal }) => ({
      face,
      normal: through(matrix, normal),
    })),
  }));
}

/// The position code of `state`: seven cubie digits 1 to 7, in the order of
/// `PLACES`, then seven twist digits 1 to 3, each the twist plus one.
export function positionCode(state) {
  const upright = normalized(state);
  const at = PLACES.map(({ at: place }) =>
    upright.find(({ position }) => same(position, place)),
  );
  return (
    at.map(({ id }) => id + 1).join("") +
    at.map((piece) => twist(piece) + 1).join("")
  );
}

/// The cubie and the twist in each place of `state`, for showing what the code
/// says: `[{place, cubie, twist}]` in the code's order.
export function readPlaces(state) {
  const upright = normalized(state);
  return PLACES.map(({ name, at: place }) => {
    const piece = upright.find(({ position }) => same(position, place));
    return { place: name, cubie: piece.id + 1, twist: twist(piece) };
  });
}

/// Every whole-cube turn: the signed permutation matrices of determinant one.
const ROTATIONS = (() => {
  const found = [];
  const orders = [
    [0, 1, 2],
    [0, 2, 1],
    [1, 0, 2],
    [1, 2, 0],
    [2, 0, 1],
    [2, 1, 0],
  ];
  for (const order of orders)
    for (let signs = 0; signs < 8; signs += 1) {
      const matrix = order.map((column, row) => {
        const line = [0, 0, 0];
        line[column] = (signs >> row) & 1 ? -1 : 1;
        return line;
      });
      const [a, b, c] = matrix;
      if (dot(cross(a, b), c) === 1) found.push(matrix);
    }
  return found;
})();

/// The state a position code names, with the fixed corner home. Throws an Error
/// for a code that is not a valid position.
export function stateFromCode(code) {
  if (typeof code !== "string" || !/^[1-7]{7}[1-3]{7}$/.test(code))
    throw new Error(
      "A position is 14 digits: seven of 1 to 7, then seven of 1 to 3",
    );
  const cubies = [...code.slice(0, 7)].map((digit) => Number(digit) - 1);
  const twists = [...code.slice(7)].map((digit) => Number(digit) - 1);
  if (new Set(cubies).size !== 7)
    throw new Error("The first seven digits must each be a different cubie");
  if (twists.reduce((sum, value) => sum + value, 0) % 3 !== 0)
    throw new Error("The twists must add up to a multiple of three");
  const pieces = cubies.map((cubie, place) => {
    const target = PLACES[place].at;
    for (const matrix of ROTATIONS) {
      const home = corner(cubie, PLACES[cubie].at);
      const moved = {
        id: cubie,
        position: through(matrix, home.position),
        stickers: home.stickers.map(({ face, normal }) => ({
          face,
          normal: through(matrix, normal),
        })),
      };
      if (same(moved.position, target) && twist(moved) === twists[place])
        return moved;
    }
    throw new Error("That position cannot be built");
  });
  return [...pieces, corner(FIXED_ID, FIXED.at)];
}

// --- Nets ---------------------------------------------------------------------

/// Where a sticker sits on its face as seen from outside with the net's
/// orientation (up on top, front at the bottom of the up face): its row and
/// column, each 0 or 1.
function cell(face, [x, y, z]) {
  switch (face) {
    case "U":
      return [z > 0 ? 1 : 0, x > 0 ? 1 : 0];
    case "D":
      return [z > 0 ? 0 : 1, x > 0 ? 1 : 0];
    case "F":
      return [y > 0 ? 0 : 1, x > 0 ? 1 : 0];
    case "B":
      return [y > 0 ? 0 : 1, x > 0 ? 0 : 1];
    case "R":
      return [y > 0 ? 0 : 1, z > 0 ? 0 : 1];
    default:
      return [y > 0 ? 0 : 1, z > 0 ? 1 : 0];
  }
}

/// What each face shows as seen from outside, sticker by sticker:
/// `{U: [[{colour, cubie, face}, ...], ...]}`, where `cubie` is the digit of
/// the cubie the sticker is on and `face` is the face it was solved on.
export function netCells(state) {
  const faces = Object.fromEntries(
    Object.keys(FACES).map((letter) => [
      letter,
      [
        [null, null],
        [null, null],
      ],
    ]),
  );
  for (const piece of state)
    for (const { face, normal } of piece.stickers) {
      const letter = FACE_OF.get(normal.join());
      const [row, column] = cell(letter, piece.position);
      faces[letter][row][column] = {
        colour: SCHEME[face],
        cubie: piece.id + 1,
        face,
      };
    }
  return faces;
}

/// The colour names on each face as seen from outside: `{U: [[a, b], [c, d]]}`.
export function net(state) {
  const faces = netCells(state);
  return Object.fromEntries(
    Object.entries(faces).map(([letter, rows]) => [
      letter,
      rows.map((row) => row.map(({ colour }) => colour)),
    ]),
  );
}
