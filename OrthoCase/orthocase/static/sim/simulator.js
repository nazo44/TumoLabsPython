// Parametric 3D orthodontic simulator.
//
// Nothing here is reconstructed from a scan or an X-ray. Both dental arches are
// generated from a handful of clinical measurements (crowding, spacing, overjet,
// overbite, midline, arch width) plus optional per-tooth tweaks, and the
// treatment is shown by easing those measurements toward their corrected values.
// All lengths are millimetres. +x = patient's left, +y = up, +z = toward the lips.

import * as THREE from '../vendor/three/three.module.min.js';
import { OrbitControls } from '../vendor/three/OrbitControls.js';
import { RoomEnvironment } from '../vendor/three/RoomEnvironment.js';

// ---------------------------------------------------------------- anatomy

// Listed from the midline outwards: central, lateral, canine, 2 premolars, 2 molars.
const SPECS = {
  U: [
    { type: 'ci', w: 8.6, h: 10.8, d: 7.0 },
    { type: 'li', w: 6.6, h: 9.4, d: 6.2 },
    { type: 'c', w: 7.6, h: 10.4, d: 8.0 },
    { type: 'pm', w: 7.0, h: 8.4, d: 9.0 },
    { type: 'pm', w: 6.6, h: 7.9, d: 9.0 },
    { type: 'm', w: 10.2, h: 7.6, d: 11.0 },
    { type: 'm', w: 9.2, h: 7.2, d: 10.6 },
  ],
  L: [
    { type: 'ci', w: 5.3, h: 8.8, d: 5.8 },
    { type: 'li', w: 5.8, h: 9.2, d: 6.1 },
    { type: 'c', w: 6.9, h: 10.4, d: 7.2 },
    { type: 'pm', w: 7.0, h: 8.4, d: 7.8 },
    { type: 'pm', w: 7.2, h: 8.0, d: 8.2 },
    { type: 'm', w: 11.0, h: 7.6, d: 10.2 },
    { type: 'm', w: 10.4, h: 7.2, d: 9.8 },
  ],
};

// Well-aligned reference position per tooth: labial crown inclination and
// mesial tip (degrees), and the height of the biting edge (mm).
const BASE = {
  U: { torque: [14, 11, 4, -2, -2, 2, 2], tip: [2, 4, 5, 1, 1, 2, 2], edge: [-1.3, -0.8, -0.9, -0.3, -0.3, -0.3, -0.3] },
  L: { torque: [11, 10, 3, -4, -6, -8, -8], tip: [0, 1, 3, 1, 1, 1, 1], edge: [0.7, 0.7, 0.6, 0.3, 0.3, 0.3, 0.3] },
};

// How a crowded arch typically looks at 10 mm of crowding: rotation (deg),
// in/out displacement (mm, + = toward the lips) and gingival displacement (mm).
const CROWD = {
  U: {
    R: [{ rot: 14, io: 0.9 }, { rot: -24, io: -2.6 }, { rot: 12, io: 2.3, v: 1.8 }, { rot: -9, io: -0.7 }, { rot: 5, io: 0.3 }, {}, {}],
    L: [{ rot: -20, io: -0.5 }, { rot: 16, io: -1.6 }, { rot: -10, io: 1.9, v: 1.2 }, { rot: 7, io: -0.4 }, { rot: -4, io: 0.2 }, {}, {}],
  },
  L: {
    R: [{ rot: 18, io: 1.3 }, { rot: -22, io: -1.9 }, { rot: 12, io: 1.4, v: 0.6 }, { rot: -8, io: -0.5 }, { rot: 4 }, {}, {}],
    L: [{ rot: 20, io: -1.4 }, { rot: -16, io: 1.2 }, { rot: -12, io: 1.1, v: 0.5 }, { rot: 6, io: -0.4 }, {}, {}, {}],
  },
};

const CROWD_SHARE = [1.0, 1.3, 1.2, 0.45, 0.2, 0, 0]; // where missing space is taken from
const SPACE_SHARE = [0.7, 1.0, 1.0, 0.6, 0.3, 0, 0]; // where extra space opens up
const MIDLINE_SHARE = [1, 1, 1, 0.7, 0.4, 0.1, 0];
const ANTERIOR = [1, 0.9, 0.45, 0.1, 0, 0, 0];
const VERTICAL = [1, 0.9, 0.55, 0.15, 0, 0, 0];

const ARCH_WIDTH = { U: 53, L: 48.5 };
const NORMAL_OVERJET = 2.5;
const NORMAL_OVERBITE = 2;
const PIVOT = 6; // centre of resistance, mm above the gum line
const ROOT = 4.5; // length of the hidden root stub
const GUM_TOP = 17.5; // height of the model base above the bite plane

export const DEFAULT_SIM = {
  crowding_upper: 0, crowding_lower: 0, spacing_upper: 0, spacing_lower: 0, diastema: 0,
  overjet: NORMAL_OVERJET, overbite: NORMAL_OVERBITE, midline: 0, narrow_upper: 0, teeth: {},
};

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const lerp = (a, b, t) => a + (b - a) * t;
const smooth = (t) => { t = clamp(t, 0, 1); return t * t * (3 - 2 * t); };
const rad = (deg) => (deg * Math.PI) / 180;

export function fdiNumber(arch, side, k) {
  const quadrant = arch === 'U' ? (side < 0 ? 1 : 2) : (side < 0 ? 4 : 3);
  return quadrant * 10 + k + 1;
}

// ---------------------------------------------------------------- arch form

// Braun's beta-function arch form, solved for depth so that the curve is
// exactly as long as the teeth that have to fit on it.
class ArchCurve {
  constructor() {
    this.n = 120;
    this.xs = new Float32Array(this.n + 1);
    this.zs = new Float32Array(this.n + 1);
    this.ss = new Float32Array(this.n + 1);
  }

  _build(width, depth) {
    const { n, xs, zs, ss } = this;
    for (let i = 0; i <= n; i++) {
      const x = (width / 2) * Math.sin(((i / n) * Math.PI) / 2);
      const u = x / width;
      xs[i] = x;
      zs[i] = depth * 3.0314 * Math.pow(Math.max(0, (0.5 + u) * (0.5 - u)), 0.8);
      ss[i] = i === 0 ? 0 : ss[i - 1] + Math.hypot(xs[i] - xs[i - 1], zs[i] - zs[i - 1]);
    }
    return ss[n];
  }

  solve(width, halfLength) {
    let lo = 2, hi = 90;
    for (let i = 0; i < 26; i++) {
      const mid = (lo + hi) / 2;
      if (this._build(width, mid) < halfLength) lo = mid; else hi = mid;
    }
    this.depth = (lo + hi) / 2;
    this.length = this._build(width, this.depth);
    return this;
  }

  // Position and unit tangent at signed arc length s (negative = patient's right).
  at(s, out) {
    const { n, xs, zs, ss } = this;
    const a = Math.abs(s), sign = s < 0 ? -1 : 1;
    let x, z, tx, tz;
    if (a >= this.length) {
      // Past the last molar the arch simply continues straight back.
      x = xs[n]; z = zs[n] - (a - this.length); tx = 0; tz = -1;
    } else {
      let lo = 0, hi = n;
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (ss[mid] <= a) lo = mid; else hi = mid; }
      const span = ss[hi] - ss[lo] || 1;
      const f = (a - ss[lo]) / span;
      x = lerp(xs[lo], xs[hi], f); z = lerp(zs[lo], zs[hi], f);
      tx = (xs[hi] - xs[lo]) / span; tz = (zs[hi] - zs[lo]) / span;
    }
    out.x = sign * x; out.z = z;
    out.tx = tx; out.tz = sign * tz; // tangent toward increasing s
    return out;
  }
}

// ---------------------------------------------------------------- teeth

const PROFILE_EXPONENT = { ci: 3.2, li: 3.0, c: 2.8, pm: 3.4, m: 4.0 };

// Cross-section of a crown at height c (0 = gum line, 1 = biting edge) and
// root depth r. Returns [half-width factor, labial z, lingual z, extra drop of
// the biting surface] — this is where each tooth type gets its character.
function crownShape(type, upper, xd, sz, c, r, spec) {
  const { h, d } = spec;
  const root = 1 - 0.5 * smooth(r * 1.4);
  let X, zLab, zLin, drop = 0;
  if (type === 'ci' || type === 'li') {
    X = lerp(upper ? 0.76 : 0.7, 1, smooth(c / 0.5));
    zLab = (d / 2) * lerp(0.94, upper ? 0.4 : 0.34, Math.pow(c, 1.5));
    const fossa = 0.16 * Math.sin(Math.PI * clamp((c - 0.25) / 0.65, 0, 1)) * (upper ? 1 : 0.6);
    zLin = -(d / 2) * (lerp(0.94, -0.12, Math.pow(smooth(c), 0.85)) - fossa);
    const top = smooth((c - 0.55) / 0.45);
    const distal = smooth((xd - 0.35) / 0.65), mesial = smooth((-xd - 0.6) / 0.4);
    drop = top * (distal * (type === 'li' ? 1.7 : 1.0) * (upper ? 1 : 0.35) + mesial * (type === 'ci' && upper ? 0.14 : 0.3));
  } else if (type === 'c') {
    X = lerp(0.74, 1, smooth(c / 0.45)) * lerp(1, 0.84, smooth((c - 0.6) / 0.4));
    zLab = (d / 2) * lerp(0.94, 0.3, Math.pow(c, 1.7));
    zLin = -(d / 2) * lerp(0.94, 0.02, Math.pow(smooth(c), 0.9));
    drop = smooth((c - 0.5) / 0.5) * h * 0.17 * Math.pow(Math.abs(xd + 0.1), 1.45);
  } else if (type === 'pm') {
    X = lerp(0.8, 1, smooth(c / 0.42)) * (1 - 0.06 * smooth((c - 0.7) / 0.3));
    const Z = lerp(0.82, 1, smooth(c / 0.4)) * (1 - 0.16 * smooth((c - 0.5) / 0.5));
    zLab = (d / 2) * Z;
    zLin = -(d / 2) * Z * (upper ? 1 : 0.92);
    const groove = Math.exp(-Math.pow(sz / 0.3, 2));
    const lingualCusp = smooth(-sz) * (upper ? 0.1 : 0.24);
    drop = smooth((c - 0.62) / 0.3) * h * (0.15 * groove + lingualCusp + 0.13 * Math.pow(Math.abs(xd), 1.6));
  } else {
    X = lerp(0.88, 1, smooth(c / 0.4)) * (1 - 0.04 * smooth((c - 0.7) / 0.3));
    const Z = lerp(0.86, 1, smooth(c / 0.4)) * (1 - 0.1 * smooth((c - 0.5) / 0.5));
    zLab = (d / 2) * Z;
    zLin = -(d / 2) * Z;
    const gz = Math.exp(-Math.pow(sz / 0.22, 2)), gx = Math.exp(-Math.pow(xd / 0.2, 2));
    const cusps = Math.pow(Math.abs(xd) - 0.5, 2) + Math.pow(Math.abs(sz) - 0.5, 2);
    drop = smooth((c - 0.62) / 0.3) * h * (0.13 * gz + 0.1 * gx - 0.06 * gz * gx + 0.12 * cusps);
  }
  return [X * root, zLab * root, zLin * root, drop];
}

const ENAMEL = {
  root: new THREE.Color(0xd3bb92),
  neck: new THREE.Color(0xe4d0a8),
  body: new THREE.Color(0xf0e6d2),
  edge: new THREE.Color(0xeeeee8),
};

// side: -1 = patient's right, +1 = left (decides which way "distal" points).
function buildToothGeometry(spec, arch, side) {
  const upper = arch === 'U';
  const gumSign = upper ? 1 : -1;
  const rings = 34, segs = 40;
  const p = PROFILE_EXPONENT[spec.type];
  const total = spec.h + ROOT;
  const bias = spec.type === 'm' || spec.type === 'pm' ? 1.25 : 1.1; // more rings on the biting surface
  const positions = [], colors = [], index = [];
  const color = new THREE.Color();

  const addVertex = (theta, phi) => {
    const dx = Math.sin(theta) * Math.cos(phi), dy = Math.cos(theta), dz = Math.sin(theta) * Math.sin(phi);
    const k = Math.pow(Math.pow(Math.abs(dx), p) + Math.pow(Math.abs(dy), p) + Math.pow(Math.abs(dz), p), -1 / p);
    const sx = dx * k, sy = dy * k, sz = dz * k;
    const g = ((1 - sy) / 2) * total; // distance from the biting edge toward the gum
    const c = clamp((spec.h - g) / spec.h, 0, 1);
    const r = clamp((g - spec.h) / ROOT, 0, 1);
    const [X, zLab, zLin, drop] = crownShape(spec.type, upper, sx, sz, c, r, spec);
    positions.push(side * sx * (spec.w / 2) * X, gumSign * (g + drop), lerp(zLin, zLab, (sz + 1) / 2));
    if (r > 0) color.copy(ENAMEL.neck).lerp(ENAMEL.root, smooth(r * 2));
    else if (c < 0.45) color.copy(ENAMEL.neck).lerp(ENAMEL.body, smooth(c / 0.45));
    else color.copy(ENAMEL.body).lerp(ENAMEL.edge, smooth((c - 0.6) / 0.4));
    // Soft shading where neighbours touch and inside the grooves.
    const shade = 1 - 0.16 * Math.pow(Math.abs(sx), 3) - Math.min(0.14, (drop / spec.h) * 0.6) * (spec.type === 'm' || spec.type === 'pm' ? 1 : 0);
    colors.push(color.r * shade, color.g * shade, color.b * shade);
  };

  addVertex(0, 0);
  for (let i = 1; i < rings; i++) {
    const theta = Math.PI * Math.pow(i / rings, bias);
    for (let j = 0; j < segs; j++) addVertex(theta, (j / segs) * Math.PI * 2);
  }
  addVertex(Math.PI, 0);
  const last = 1 + (rings - 1) * segs;
  const at = (i, j) => 1 + (i - 1) * segs + (j % segs);
  for (let j = 0; j < segs; j++) {
    index.push(0, at(1, j), at(1, j + 1));
    index.push(last, at(rings - 1, j + 1), at(rings - 1, j));
  }
  for (let i = 1; i < rings - 1; i++) {
    for (let j = 0; j < segs; j++) {
      index.push(at(i, j), at(i + 1, j), at(i, j + 1));
      index.push(at(i, j + 1), at(i + 1, j), at(i + 1, j + 1));
    }
  }

  // Mirroring for side/arch can turn the surface inside out; fix by signed volume.
  let volume = 0;
  for (let t = 0; t < index.length; t += 3) {
    const a = index[t] * 3, b = index[t + 1] * 3, c = index[t + 2] * 3;
    volume += positions[a] * (positions[b + 1] * positions[c + 2] - positions[b + 2] * positions[c + 1])
      - positions[a + 1] * (positions[b] * positions[c + 2] - positions[b + 2] * positions[c])
      + positions[a + 2] * (positions[b] * positions[c + 1] - positions[b + 1] * positions[c]);
  }
  if (volume < 0) for (let t = 0; t < index.length; t += 3) { const tmp = index[t + 1]; index[t + 1] = index[t + 2]; index[t + 2] = tmp; }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geometry.setIndex(index);
  geometry.computeVertexNormals();
  return geometry;
}

// Where a bracket sits: middle of the labial face, following its slope.
function bracketSeat(spec, arch) {
  const upper = arch === 'U';
  const face = (c) => crownShape(spec.type, upper, 0, 1, c, 0, spec)[1];
  const slope = (face(0.36) - face(0.64)) / (0.28 * spec.h); // dz per mm toward the gum
  return { y: (upper ? 1 : -1) * spec.h * 0.5, z: face(0.5), tilt: Math.atan(slope) * (upper ? 1 : -1) };
}

// ---------------------------------------------------------------- braces

// Bonding pad and bracket body as one geometry, drawn once per arch (instanced).
function buildBracketGeometry() {
  const positions = [], normals = [];
  for (const [w, h, d, z] of [[2.7, 2.4, 0.45, 0.225], [1.9, 1.7, 0.9, 0.85]]) {
    const box = new THREE.BoxGeometry(w, h, d).toNonIndexed();
    box.translate(0, 0, z);
    positions.push(...box.attributes.position.array);
    normals.push(...box.attributes.normal.array);
    box.dispose();
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute(normals, 3));
  return geometry;
}

const WIRE_STEPS = 4; // rings per span between brackets
const WIRE_SIDES = 6;
const WIRE_RADIUS = 0.23;

// Archwire: a thin tube through the 14 bracket slots, rewritten in place each pose.
class Wire {
  constructor(material) {
    this.rings = 15 * WIRE_STEPS + 1;
    const count = this.rings * WIRE_SIDES;
    this.positions = new Float32Array(count * 3);
    this.normals = new Float32Array(count * 3);
    this.ctrl = new Float32Array(18 * 3); // 14 slots, 2 free ends, 2 spline phantoms
    this.path = new Float32Array(this.rings * 3);
    const index = [];
    for (let i = 0; i < this.rings - 1; i++) {
      for (let j = 0; j < WIRE_SIDES; j++) {
        const a = i * WIRE_SIDES + j, b = i * WIRE_SIDES + ((j + 1) % WIRE_SIDES);
        index.push(a, a + WIRE_SIDES, b, b, a + WIRE_SIDES, b + WIRE_SIDES);
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(this.positions, 3));
    geometry.setAttribute('normal', new THREE.BufferAttribute(this.normals, 3));
    geometry.setIndex(index);
    this.mesh = new THREE.Mesh(geometry, material);
    this.mesh.frustumCulled = false;
  }

  // seats: 14 world-space points, patient's right molar -> left molar.
  update(seats) {
    const { ctrl, path, positions, normals, rings } = this;
    for (let i = 0; i < 14; i++) { ctrl[(i + 2) * 3] = seats[i].x; ctrl[(i + 2) * 3 + 1] = seats[i].y; ctrl[(i + 2) * 3 + 2] = seats[i].z; }
    const extend = (to, from, next, length) => {
      const dx = ctrl[from * 3] - ctrl[next * 3], dy = ctrl[from * 3 + 1] - ctrl[next * 3 + 1], dz = ctrl[from * 3 + 2] - ctrl[next * 3 + 2];
      const scale = length / (Math.hypot(dx, dy, dz) || 1);
      ctrl[to * 3] = ctrl[from * 3] + dx * scale; ctrl[to * 3 + 1] = ctrl[from * 3 + 1] + dy * scale; ctrl[to * 3 + 2] = ctrl[from * 3 + 2] + dz * scale;
    };
    extend(1, 2, 3, 2.2); extend(0, 1, 2, 2.2);
    extend(16, 15, 14, 2.2); extend(17, 16, 15, 2.2);

    for (let r = 0; r < rings; r++) {
      const u = r / WIRE_STEPS, i = Math.min(14, Math.floor(u)), f = u - i, e = (i + 1) * 3;
      for (let axis = 0; axis < 3; axis++) path[r * 3 + axis] = catmull(ctrl[e - 3 + axis], ctrl[e + axis], ctrl[e + 3 + axis], ctrl[e + 6 + axis], f);
    }
    for (let r = 0; r < rings; r++) {
      const a = Math.max(0, r - 1) * 3, b = Math.min(rings - 1, r + 1) * 3;
      let tx = path[b] - path[a], ty = path[b + 1] - path[a + 1], tz = path[b + 2] - path[a + 2];
      const tl = Math.hypot(tx, ty, tz) || 1; tx /= tl; ty /= tl; tz /= tl;
      // First axis: horizontal, across the wire. Second: perpendicular to both.
      let ax = -tz, az = tx;
      const al = Math.hypot(ax, az) || 1; ax /= al; az /= al;
      const bx = -ty * az, by = tx * az - tz * ax, bz = ty * ax;
      for (let j = 0; j < WIRE_SIDES; j++) {
        const angle = (j / WIRE_SIDES) * Math.PI * 2, c = Math.cos(angle), sn = Math.sin(angle);
        const nx = c * ax + sn * bx, ny = sn * by, nz = c * az + sn * bz, o = (r * WIRE_SIDES + j) * 3;
        positions[o] = path[r * 3] + nx * WIRE_RADIUS; positions[o + 1] = path[r * 3 + 1] + ny * WIRE_RADIUS; positions[o + 2] = path[r * 3 + 2] + nz * WIRE_RADIUS;
        normals[o] = nx; normals[o + 1] = ny; normals[o + 2] = nz;
      }
    }
    this.mesh.geometry.attributes.position.needsUpdate = true;
    this.mesh.geometry.attributes.normal.needsUpdate = true;
  }
}

// ---------------------------------------------------------------- gums

const GUM_PROFILE = 12; // points across the gum, lip side to tongue side
const GUM_SEG = 8; // stations per tooth
const GUM_EXT = 3; // extra stations behind the last molar
const PALATE_DIV = 10;

const GUM_COLORS = [0xc96d7c, 0xd17886, 0xdb8590, 0xe6939b, 0xee9fa4, 0xf2a8ab, 0xeb9ea5, 0xeb9ea5, 0xf0a5a9, 0xe9989f, 0xdf8b95, 0xd88390]
  .map((hex) => new THREE.Color(hex));

function catmull(p0, p1, p2, p3, t) {
  const t2 = t * t, t3 = t2 * t;
  return 0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3);
}

class Gum {
  constructor(arch, material) {
    this.arch = arch;
    this.gumSign = arch === 'U' ? 1 : -1;
    this.vault = arch === 'U' ? 4.5 : 1.5;
    const S = (this.stations = 14 * GUM_SEG + 1 + 2 * GUM_EXT);
    const P = GUM_PROFILE, K = PALATE_DIV, half = (S - 1) / 2;

    // Vertex layout: tube, flat-top duplicates, palate interior, back wall, end caps.
    this.oTube = 0;
    this.oTop = S * P;
    this.oPalate = this.oTop + S;
    this.oBack = this.oPalate + (half + 1) * (K - 1);
    this.oCaps = this.oBack + 2 * (K + 1);
    const count = this.oCaps + 2 * (P + 1);
    this.positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);
    const paint = (i, color) => { colors[i * 3] = color.r; colors[i * 3 + 1] = color.g; colors[i * 3 + 2] = color.b; };
    const base = GUM_COLORS[0], palate = new THREE.Color(0xe0929b);
    for (let k = 0; k < S; k++) {
      for (let q = 0; q < P; q++) paint(k * P + q, GUM_COLORS[q]);
      paint(this.oTop + k, base);
    }
    for (let i = this.oPalate; i < this.oBack; i++) paint(i, palate);
    for (let i = this.oBack; i < count; i++) paint(i, base);

    const index = [];
    const tri = this.gumSign > 0 ? (a, b, c) => index.push(a, b, c) : (a, b, c) => index.push(a, c, b);
    const tube = (k, q) => k * P + q;
    for (let k = 0; k < S - 1; k++) {
      for (let q = 0; q < P - 1; q++) {
        tri(tube(k, q), tube(k, q + 1), tube(k + 1, q));
        tri(tube(k, q + 1), tube(k + 1, q + 1), tube(k + 1, q));
      }
    }
    // Palate / floor of the mouth, spanning between the two tongue-side edges.
    const pal = (k, j) => (j === 0 ? tube(k, P - 1) : j === K ? tube(S - 1 - k, P - 1) : this.oPalate + k * (K - 1) + (j - 1));
    for (let k = 0; k < half; k++) {
      for (let j = 0; j < K; j++) {
        tri(pal(k, j), pal(k, j + 1), pal(k + 1, j));
        tri(pal(k, j + 1), pal(k + 1, j + 1), pal(k + 1, j));
      }
    }
    // Flat base of the model.
    for (let k = 0; k < half; k++) {
      const a = this.oTop + k, b = this.oTop + S - 1 - k, a2 = this.oTop + k + 1, b2 = this.oTop + S - 2 - k;
      tri(a, a2, b);
      tri(b, a2, b2);
    }
    // Wall closing the back of the model.
    for (let j = 0; j < K; j++) {
      const t0 = this.oBack + j, t1 = t0 + 1, b0 = this.oBack + K + 1 + j, b1 = b0 + 1;
      tri(t0, t1, b0);
      tri(t1, b1, b0);
    }
    // End caps behind the last molars.
    for (let e = 0; e < 2; e++) {
      const o = this.oCaps + e * (P + 1), centre = o + P;
      for (let q = 0; q < P; q++) {
        const a = o + q, b = o + ((q + 1) % P);
        if (e === 0) tri(centre, b, a); else tri(centre, a, b);
      }
    }

    this.geometry = new THREE.BufferGeometry();
    this.geometry.setAttribute('position', new THREE.BufferAttribute(this.positions, 3));
    this.geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    this.geometry.setIndex(index);
    this.mesh = new THREE.Mesh(this.geometry, material);
    this.mesh.frustumCulled = false;

    this.cx = new Float32Array(18); this.cz = new Float32Array(18);
    this.sx = new Float32Array(S); this.sz = new Float32Array(S);
  }

  // poses: 14 entries, patient's right molar -> left molar, each with the neck
  // centre (x, z), cervical height gc, half-depths wLab/wLin, width w, papilla.
  update(poses) {
    const S = this.stations, P = GUM_PROFILE, K = PALATE_DIV, half = (S - 1) / 2;
    const n = poses.length, pos = this.positions, sign = this.gumSign;
    const { cx, cz, sx, sz } = this;

    // Smooth the neck line so the gum flows; teeth that stick out of it get a bulge.
    const dev = [];
    for (let i = 0; i < n; i++) {
      const a = poses[Math.max(0, i - 1)], b = poses[i], c = poses[Math.min(n - 1, i + 1)];
      const edge = i === 0 || i === n - 1;
      cx[i + 2] = edge ? b.x : 0.25 * a.x + 0.5 * b.x + 0.25 * c.x;
      cz[i + 2] = edge ? b.z : 0.25 * a.z + 0.5 * b.z + 0.25 * c.z;
      dev.push((b.x - cx[i + 2]) * b.nx + (b.z - cz[i + 2]) * b.nz);
    }
    const ghost = (to, from, next, width) => {
      const dx = cx[from] - cx[next], dz = cz[from] - cz[next], len = Math.hypot(dx, dz) || 1;
      cx[to] = cx[from] + (dx / len) * width; cz[to] = cz[from] + (dz / len) * width;
    };
    ghost(1, 2, 3, poses[0].w); ghost(0, 1, 2, poses[0].w);
    ghost(n + 2, n + 1, n, poses[n - 1].w); ghost(n + 3, n + 2, n + 1, poses[n - 1].w);

    const u0 = -0.5 - GUM_EXT / GUM_SEG;
    for (let k = 0; k < S; k++) {
      const u = u0 + k / GUM_SEG;
      const i = Math.floor(u), f = u - i, e = i + 2;
      sx[k] = catmull(cx[e - 1], cx[e], cx[e + 1], cx[e + 2], f);
      sz[k] = catmull(cz[e - 1], cz[e], cz[e + 1], cz[e + 2], f);
    }

    for (let k = 0; k < S; k++) {
      const u = u0 + k / GUM_SEG;
      const ka = Math.max(0, k - 1), kb = Math.min(S - 1, k + 1);
      let tx = sx[kb] - sx[ka], tz = sz[kb] - sz[ka];
      const tl = Math.hypot(tx, tz) || 1; tx /= tl; tz /= tl;
      const nx = -tz, nz = tx; // points toward the lips/cheeks

      const uc = clamp(u, 0, n - 1);
      const ia = Math.min(n - 2, Math.floor(uc)), ff = uc - ia;
      const A = poses[ia], B = poses[ia + 1];
      const wLab = lerp(A.wLab, B.wLab, ff) + Math.max(0, lerp(dev[ia], dev[ia + 1], ff));
      const wLin = lerp(A.wLin, B.wLin, ff) + Math.max(0, -lerp(dev[ia], dev[ia + 1], ff));
      const gc = lerp(A.gc, B.gc, smooth(ff));
      const ph = lerp(A.papilla, B.papilla, ff);
      const beyond = u < -0.5 || u > n - 0.5;
      const pf = beyond ? 0.6 : Math.pow(Math.abs(u - Math.round(u)) * 2, 1.6);
      const m = gc - ph * pf, ml = gc - ph * 0.85 * pf, crest = m - 0.25;
      const deep = gc - ph * 0.45; // the scallop fades out away from the gum line

      const ring = [
        [wLab + 2.6, GUM_TOP], [wLab + 2.2, deep + 5.8], [wLab + 1.3, lerp(m, deep, 0.5) + 3.0], [wLab + 0.45, m + 1.4],
        [wLab * 0.98 + 0.05, m + 0.55], [wLab * 0.8, m], [wLab * 0.35, crest], [-wLin * 0.35, crest],
        [-wLin * 0.8, ml], [-wLin * 0.98 - 0.05, ml + 0.6], [-wLin - 0.9, lerp(ml, deep, 0.6) + 2.0], [-wLin - 2.2, deep + 3.8],
      ];
      for (let q = 0; q < P; q++) {
        const o = (k * P + q) * 3;
        pos[o] = sx[k] + nx * ring[q][0];
        pos[o + 1] = sign * Math.min(ring[q][1], GUM_TOP);
        pos[o + 2] = sz[k] + nz * ring[q][0];
      }
      const top = (this.oTop + k) * 3;
      pos[top] = pos[k * P * 3]; pos[top + 1] = sign * GUM_TOP; pos[top + 2] = pos[k * P * 3 + 2];
    }

    const tubeAt = (k, q) => (k * P + q) * 3;
    for (let k = 0; k <= half; k++) {
      const a = tubeAt(k, P - 1), b = tubeAt(S - 1 - k, P - 1);
      const span = Math.hypot(pos[b] - pos[a], pos[b + 2] - pos[a + 2]);
      const rise = Math.min(this.vault, span * 0.2);
      for (let j = 1; j < K; j++) {
        const f = j / K, o = (this.oPalate + k * (K - 1) + (j - 1)) * 3;
        pos[o] = lerp(pos[a], pos[b], f);
        pos[o + 1] = lerp(pos[a + 1], pos[b + 1], f) + sign * rise * Math.sin(Math.PI * f);
        pos[o + 2] = lerp(pos[a + 2], pos[b + 2], f);
      }
    }

    // Back wall: straight top edge, bottom edge following the palate's back rim.
    const topA = (this.oTop) * 3, topB = (this.oTop + S - 1) * 3;
    for (let j = 0; j <= K; j++) {
      const f = j / K, t = (this.oBack + j) * 3, b = (this.oBack + K + 1 + j) * 3;
      pos[t] = lerp(pos[topA], pos[topB], f); pos[t + 1] = sign * GUM_TOP; pos[t + 2] = lerp(pos[topA + 2], pos[topB + 2], f);
      const src = j === 0 ? tubeAt(0, P - 1) : j === K ? tubeAt(S - 1, P - 1) : (this.oPalate + (j - 1)) * 3;
      pos[b] = pos[src]; pos[b + 1] = pos[src + 1]; pos[b + 2] = pos[src + 2];
    }

    for (let e = 0; e < 2; e++) {
      const k = e === 0 ? 0 : S - 1, o = this.oCaps + e * (P + 1);
      let mx = 0, my = 0, mz = 0;
      for (let q = 0; q < P; q++) {
        const src = tubeAt(k, q), dst = (o + q) * 3;
        pos[dst] = pos[src]; pos[dst + 1] = pos[src + 1]; pos[dst + 2] = pos[src + 2];
        mx += pos[src]; my += pos[src + 1]; mz += pos[src + 2];
      }
      const c = (o + P) * 3;
      pos[c] = mx / P; pos[c + 1] = my / P; pos[c + 2] = mz / P;
    }

    this.geometry.attributes.position.needsUpdate = true;
    this.geometry.computeVertexNormals();
  }
}

// ---------------------------------------------------------------- simulator

const VIEWS = {
  front: { position: [0, 3, 128], target: [0, 0, 16], show: 'both' },
  right: { position: [-118, 4, 42], target: [0, 0, 16], show: 'both' },
  left: { position: [118, 4, 42], target: [0, 0, 16], show: 'both' },
  upper: { position: [0, -128, 20.6], target: [0, 0, 20], show: 'U' },
  lower: { position: [0, 128, 20.6], target: [0, 0, 20], show: 'L' },
};

export class OrthoSimulator {
  constructor(container, { editable = false, onSelectTooth = null } = {}) {
    this.container = container;
    this.editable = editable;
    this.onSelectTooth = onSelectTooth;
    this.params = { ...DEFAULT_SIM, teeth: {} };
    this.outcome = { align: 1, bite: 1 };
    this.appliance = 'none';
    this.progress = 0;
    this.duration = 7000; // ms for a full play-through
    this.playing = false;
    this.compare = false;
    this.show = 'both';
    this.selected = null;
    this.progressListeners = [];
    this._dirty = true;
    this._disposed = false;

    const renderer = (this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }));
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.92;
    renderer.setClearColor(0x000000, 0);
    renderer.domElement.classList.add('sim-canvas');
    container.appendChild(renderer.domElement);

    const scene = (this.scene = new THREE.Scene());
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    pmrem.dispose();
    scene.add(new THREE.HemisphereLight(0xffffff, 0xcdb9b0, 0.4));
    const key = new THREE.DirectionalLight(0xffffff, 1.35);
    key.position.set(40, 70, 140);
    scene.add(key);
    const under = new THREE.DirectionalLight(0xffffff, 0.4); // keeps the upper gum from going flat
    under.position.set(-20, -70, 110);
    scene.add(under);
    const rim = new THREE.DirectionalLight(0xfff0e2, 0.5);
    rim.position.set(-90, 20, -30);
    scene.add(rim);

    this.camera = new THREE.PerspectiveCamera(28, 1, 10, 600);
    this.camera.position.set(...VIEWS.front.position);
    this.controls = new OrbitControls(this.camera, renderer.domElement);
    this.controls.target.set(...VIEWS.front.target);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.enablePan = false;
    this.controls.minDistance = 70;
    this.controls.maxDistance = 260;
    this.controls.addEventListener('change', () => { this._dirty = true; });

    this.materials = {
      enamel: new THREE.MeshPhysicalMaterial({ vertexColors: true, roughness: 0.24, clearcoat: 0.55, clearcoatRoughness: 0.22, envMapIntensity: 0.9 }),
      selected: new THREE.MeshPhysicalMaterial({ vertexColors: true, color: 0x7fd9cd, roughness: 0.24, clearcoat: 0.55, clearcoatRoughness: 0.22 }),
      gum: new THREE.MeshPhysicalMaterial({ vertexColors: true, roughness: 0.42, clearcoat: 0.35, clearcoatRoughness: 0.4, envMapIntensity: 0.7 }),
      metal: new THREE.MeshStandardMaterial({ color: 0xc8cdd6, metalness: 1, roughness: 0.3 }),
      ceramic: new THREE.MeshStandardMaterial({ color: 0xf4f0e6, roughness: 0.22, metalness: 0.05 }),
      wire: new THREE.MeshStandardMaterial({ color: 0xaab1bd, metalness: 1, roughness: 0.25, side: THREE.DoubleSide }),
      aligner: new THREE.MeshStandardMaterial({ color: 0xdcefff, roughness: 0.08, transparent: true, opacity: 0.3, depthWrite: false }),
    };

    this._buildModel();
    this._bindPointer();

    this._resizeObserver = new ResizeObserver(() => this._resize());
    this._resizeObserver.observe(container);
    this._resize();
    this._last = performance.now();
    this._frame = this._frame.bind(this);
    this._raf = requestAnimationFrame(this._frame);
  }

  // ------------------------------------------------------------ build

  _buildModel() {
    this.curves = { U: new ArchCurve(), L: new ArchCurve() };
    this.groups = { U: new THREE.Group(), L: new THREE.Group() };
    this.scene.add(this.groups.U, this.groups.L);

    const bracketGeometry = buildBracketGeometry();
    const tilt = new THREE.Quaternion(), euler = new THREE.Euler();

    this.teeth = [];
    this.gums = {};
    this.wires = {};
    this.brackets = {};
    this._seats = Array.from({ length: 14 }, () => new THREE.Vector3());
    this._matrix = new THREE.Matrix4();
    for (const arch of ['U', 'L']) {
      for (let i = 0; i < 14; i++) {
        const side = i < 7 ? -1 : 1;
        const k = i < 7 ? 6 - i : i - 7;
        const spec = SPECS[arch][k];
        const gumSign = arch === 'U' ? 1 : -1;
        const holder = new THREE.Group();
        const pivot = new THREE.Group();
        pivot.position.y = gumSign * (spec.h + PIVOT);
        const mesh = new THREE.Mesh(buildToothGeometry(spec, arch, side), this.materials.enamel);
        mesh.position.y = -gumSign * (spec.h + PIVOT);
        pivot.add(mesh);
        holder.add(pivot);
        this.groups[arch].add(holder);

        // Where this tooth's bracket sits, in the tooth's own frame (molars get a wider tube).
        const seat = bracketSeat(spec, arch);
        const molar = spec.type === 'm';
        const bracketSeatMatrix = new THREE.Matrix4().compose(
          new THREE.Vector3(0, seat.y, seat.z),
          tilt.setFromEuler(euler.set(seat.tilt, 0, 0)),
          new THREE.Vector3(molar ? 1.45 : 1, molar ? 0.9 : 1, molar ? 1.3 : 1),
        );

        const shell = new THREE.Mesh(mesh.geometry, this.materials.aligner);
        shell.scale.setScalar(1.07);
        shell.position.y = gumSign * spec.h * 0.5 * (1 - 1.07);
        shell.renderOrder = 2;
        shell.visible = false;
        mesh.add(shell);

        const fdi = fdiNumber(arch, side, k);
        mesh.userData.fdi = fdi;
        this.teeth.push({ arch, side, k, spec, gumSign, fdi, holder, pivot, mesh, shell, seat: bracketSeatMatrix, wireSeat: new THREE.Vector3(0, seat.y, seat.z + 0.95) });
      }
      this.gums[arch] = new Gum(arch, this.materials.gum);
      this.groups[arch].add(this.gums[arch].mesh);
      this.wires[arch] = new Wire(this.materials.wire);
      this.brackets[arch] = new THREE.InstancedMesh(bracketGeometry, this.materials.metal, 14);
      this.brackets[arch].instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      this.brackets[arch].frustumCulled = false;
      this.scene.add(this.wires[arch].mesh, this.brackets[arch]);
    }

    // Reference arch depths: the lower arch is placed so that a well-aligned
    // bite has the normal overjet.
    const sum = (arch) => SPECS[arch].reduce((t, s) => t + s.w, 0);
    this.idealDepth = {
      U: this.curves.U.solve(ARCH_WIDTH.U, sum('U')).depth,
      L: this.curves.L.solve(ARCH_WIDTH.L, sum('L')).depth,
    };
    this.lowerZ0 = this.idealDepth.U - this.idealDepth.L - 2.0;
  }

  // ------------------------------------------------------------ pose

  _effective(t) {
    const p = this.params;
    const a = this.outcome.align * smooth(t / 0.62); // teeth straighten first...
    const b = this.outcome.bite * smooth((t - 0.28) / 0.72); // ...then the bite settles
    return {
      crowd: { U: p.crowding_upper * (1 - a), L: p.crowding_lower * (1 - a) },
      space: { U: p.spacing_upper * (1 - a), L: p.spacing_lower * (1 - a) },
      diastema: p.diastema * (1 - a),
      midline: p.midline * (1 - a),
      overjet: NORMAL_OVERJET + (p.overjet - NORMAL_OVERJET) * (1 - b),
      overbite: NORMAL_OVERBITE + (p.overbite - NORMAL_OVERBITE) * (1 - b),
      narrow: p.narrow_upper * (1 - b),
      tooth: 1 - a,
    };
  }

  _layoutArch(arch, e) {
    const specs = SPECS[arch], base = BASE[arch];
    const crowd = e.crowd[arch], space = e.space[arch];
    const diastema = arch === 'U' ? e.diastema : 0;
    const crowdTotal = CROWD_SHARE.reduce((t, v) => t + v, 0), spaceTotal = SPACE_SHARE.reduce((t, v) => t + v, 0);
    const slots = specs.map((s, k) => s.w - (crowd / 2) * (CROWD_SHARE[k] / crowdTotal) + (space / 2) * (SPACE_SHARE[k] / spaceTotal));
    const halfLength = slots.reduce((t, v) => t + v, 0) + diastema / 2;
    const width = ARCH_WIDTH[arch] - (arch === 'U' ? e.narrow : 0);
    const curve = this.curves[arch].solve(width, halfLength);

    const severity = Math.pow(clamp(crowd / 10, 0, 1.3), 0.8);
    const jetExcess = e.overjet - NORMAL_OVERJET, biteExcess = e.overbite - NORMAL_OVERBITE;
    const gumSign = arch === 'U' ? 1 : -1;
    const poses = new Array(14);
    const point = {};

    for (const tooth of this.teeth) {
      if (tooth.arch !== arch) continue;
      const { side, k, spec } = tooth;
      let along = diastema / 2;
      for (let j = 0; j < k; j++) along += slots[j];
      along += slots[k] / 2;
      let s = side * along;
      if (arch === 'U') s += e.midline * MIDLINE_SHARE[k];
      curve.at(s, point);
      const nx = -point.tz, nz = point.tx;

      const pattern = CROWD[arch][side < 0 ? 'R' : 'L'][k];
      const override = this.params.teeth[tooth.fdi] || {};
      const rot = (pattern.rot || 0) * severity + (override.rot || 0) * e.tooth;
      let inout = (pattern.io || 0) * severity + (override.inout || 0) * e.tooth;
      const gingival = (pattern.v || 0) * severity - (override.vert || 0) * e.tooth;
      const tip = base.tip[k] + (override.tip || 0) * e.tooth;

      let torque = base.torque[k];
      // Keep the contact points on the arch line despite the crown's lean.
      inout -= (PIVOT + 0.65 * spec.h) * Math.sin(rad(base.torque[k]));
      if (arch === 'U') torque += clamp(jetExcess * 2.0, -14, 24) * ANTERIOR[k];

      let edge = base.edge[k] + gumSign * gingival;
      edge += (arch === 'U' ? -1 : 1) * 0.5 * biteExcess * VERTICAL[k];

      tooth.holder.position.set(point.x + nx * inout, edge, point.z + nz * inout);
      tooth.holder.rotation.y = Math.atan2(-point.tz, point.tx);
      tooth.pivot.rotation.set(-gumSign * rad(torque), 0, -gumSign * side * rad(tip));
      tooth.mesh.rotation.y = rad(rot);

      const neck = inout + PIVOT * Math.sin(rad(torque));
      const i = side < 0 ? 6 - k : 7 + k;
      poses[i] = {
        x: point.x + nx * neck, z: point.z + nz * neck, nx, nz,
        gc: gumSign * edge + spec.h, wLab: spec.d * 0.475, wLin: spec.d * 0.475, w: spec.w,
        papilla: k < 3 ? 3.0 : 2.1,
      };
    }
    this.gums[arch].update(poses);
    return curve.depth;
  }

  _applyPose(t, withAppliance) {
    const e = this._effective(t);
    const depthU = this._layoutArch('U', e);
    const depthL = this._layoutArch('L', e);
    const jetExcess = e.overjet - NORMAL_OVERJET;
    const drift = 0.5 * ((depthU - this.idealDepth.U) - (depthL - this.idealDepth.L));
    const lowerZ = this.lowerZ0 - 0.6 * jetExcess + drift;
    this.groups.L.position.z = lowerZ;

    const appliance = withAppliance ? this.appliance : 'none';
    const braces = appliance === 'metal' || appliance === 'ceramic';
    const material = appliance === 'ceramic' ? this.materials.ceramic : this.materials.metal;
    for (const tooth of this.teeth) {
      tooth.shell.visible = appliance === 'aligners';
      tooth.mesh.material = tooth.fdi === this.selected ? this.materials.selected : this.materials.enamel;
    }
    this.groups.U.visible = this.show !== 'L';
    this.groups.L.visible = this.show !== 'U';
    if (braces) this.scene.updateMatrixWorld(true);
    for (const arch of ['U', 'L']) {
      const on = braces && this.groups[arch].visible;
      this.brackets[arch].visible = on;
      this.wires[arch].mesh.visible = on;
      if (on) this._poseBraces(arch, material);
    }
  }

  // Brackets ride on their teeth; the wire threads through the bracket slots.
  _poseBraces(arch, material) {
    const brackets = this.brackets[arch];
    brackets.material = material;
    let i = 0;
    for (const tooth of this.teeth) {
      if (tooth.arch !== arch) continue; // teeth are stored right molar -> left molar per arch
      brackets.setMatrixAt(i, this._matrix.multiplyMatrices(tooth.mesh.matrixWorld, tooth.seat));
      this._seats[i].copy(tooth.wireSeat).applyMatrix4(tooth.mesh.matrixWorld);
      i += 1;
    }
    brackets.instanceMatrix.needsUpdate = true;
    this.wires[arch].update(this._seats);
  }

  // ------------------------------------------------------------ render loop

  _resize() {
    const width = this.container.clientWidth || 300, height = this.container.clientHeight || 300;
    this.renderer.setSize(width, height, false);
    this._size = { width, height };
    this._dirty = true;
  }

  _showAppliance(t) {
    return t > 0.02 && t < 0.985;
  }

  _render() {
    const { width, height } = this._size;
    const renderer = this.renderer;
    if (this.compare) {
      const halfWidth = Math.floor(width / 2);
      this.camera.aspect = halfWidth / height;
      this.camera.updateProjectionMatrix();
      renderer.setScissorTest(true);
      renderer.setViewport(0, 0, halfWidth, height);
      renderer.setScissor(0, 0, halfWidth, height);
      this._applyPose(0, false);
      renderer.render(this.scene, this.camera);
      renderer.setViewport(halfWidth, 0, width - halfWidth, height);
      renderer.setScissor(halfWidth, 0, width - halfWidth, height);
      this._applyPose(this.progress, this._showAppliance(this.progress));
      renderer.render(this.scene, this.camera);
      renderer.setScissorTest(false);
    } else {
      this.camera.aspect = width / height;
      this.camera.updateProjectionMatrix();
      renderer.setViewport(0, 0, width, height);
      this._applyPose(this.progress, this._showAppliance(this.progress));
      renderer.render(this.scene, this.camera);
    }
  }

  _frame(now) {
    if (this._disposed) return;
    const elapsed = now - this._last;
    // Playback follows the clock, so a slow device drops frames instead of playing in slow motion.
    const dt = Math.min(400, elapsed);
    this._last = now;
    if (this.playing) {
      // Sustained slow frames: step the render resolution down (2x -> 1.5x -> 1x).
      this._slow = elapsed > 55 ? (this._slow || 0) + 1 : 0;
      const ratio = this.renderer.getPixelRatio();
      if (this._slow > 10 && ratio > 1) { this.renderer.setPixelRatio(ratio > 1.5 ? 1.5 : 1); this._slow = 0; this._resize(); }
      const next = this.progress + dt / this.duration;
      if (next >= 1) { this._setProgress(1); this.playing = false; this._emit(); } else this._setProgress(next);
    }
    if (this._tween) this._stepTween(now);
    this.controls.update();
    if (this._dirty) { this._dirty = false; this._render(); }
    this._raf = requestAnimationFrame(this._frame);
  }

  _setProgress(t) {
    this.progress = clamp(t, 0, 1);
    this._dirty = true;
    this._emit();
  }

  _emit() {
    for (const listener of this.progressListeners) listener(this.progress, this.playing);
  }

  // ------------------------------------------------------------ camera

  _stepTween(now) {
    const tw = this._tween;
    const f = smooth((now - tw.start) / tw.ms);
    this.camera.position.lerpVectors(tw.fromPos, tw.toPos, f);
    this.controls.target.lerpVectors(tw.fromTarget, tw.toTarget, f);
    this._dirty = true;
    if (f >= 1) this._tween = null;
  }

  setView(name, animate = true) {
    const view = VIEWS[name] || VIEWS.front;
    this.show = view.show;
    const toPos = new THREE.Vector3(...view.position), toTarget = new THREE.Vector3(...view.target);
    // Narrow (portrait) viewports need the camera further back to fit the arch.
    const aspect = (this._size.width / (this.compare ? 2 : 1)) / this._size.height;
    if (aspect < 1.25) toPos.sub(toTarget).multiplyScalar(clamp(1.25 / aspect, 1, 1.7)).add(toTarget);
    if (animate) {
      this._tween = { start: performance.now(), ms: 520, fromPos: this.camera.position.clone(), toPos, fromTarget: this.controls.target.clone(), toTarget };
    } else {
      this.camera.position.copy(toPos);
      this.controls.target.copy(toTarget);
    }
    this.view = name;
    this._dirty = true;
  }

  // ------------------------------------------------------------ picking

  _bindPointer() {
    if (!this.editable) return;
    const dom = this.renderer.domElement;
    let down = null;
    dom.addEventListener('pointerdown', (ev) => { down = { x: ev.clientX, y: ev.clientY }; });
    dom.addEventListener('pointerup', (ev) => {
      if (!down || Math.hypot(ev.clientX - down.x, ev.clientY - down.y) > 5 || this.compare) return;
      const rect = dom.getBoundingClientRect();
      const ndc = new THREE.Vector2(((ev.clientX - rect.left) / rect.width) * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1);
      const ray = new THREE.Raycaster();
      ray.setFromCamera(ndc, this.camera);
      const meshes = this.teeth.filter((t) => this.groups[t.arch].visible).map((t) => t.mesh);
      const hit = ray.intersectObjects(meshes, false)[0];
      this.selectTooth(hit ? hit.object.userData.fdi : null);
      if (this.onSelectTooth) this.onSelectTooth(this.selected);
    });
  }

  // ------------------------------------------------------------ public API

  setParams(sim) {
    this.params = { ...DEFAULT_SIM, ...(sim || {}), teeth: { ...((sim && sim.teeth) || {}) } };
    this._dirty = true;
  }

  // Share of the alignment and bite problems the chosen treatment corrects (0-100).
  setOutcome(alignPct = 100, bitePct = 100) {
    this.outcome = { align: clamp(alignPct, 0, 100) / 100, bite: clamp(bitePct, 0, 100) / 100 };
    this._dirty = true;
  }

  setAppliance(name) {
    this.appliance = ['metal', 'ceramic', 'aligners'].includes(name) ? name : 'none';
    this._dirty = true;
  }

  setProgress(t) { this.playing = false; this._setProgress(t); }

  play() {
    if (this.progress >= 0.999) this.progress = 0;
    this.playing = true;
    this._last = performance.now();
    this._emit();
  }

  pause() { this.playing = false; this._emit(); }

  onProgress(listener) { this.progressListeners.push(listener); }

  setCompare(on) {
    this.compare = !!on;
    this.setView(this.view || 'front', false);
  }

  selectTooth(fdi) { this.selected = fdi || null; this._dirty = true; }

  dispose() {
    this._disposed = true;
    cancelAnimationFrame(this._raf);
    this._resizeObserver.disconnect();
    this.controls.dispose();
    this.scene.traverse((object) => { if (object.geometry) object.geometry.dispose(); });
    for (const material of Object.values(this.materials)) material.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}

// ---------------------------------------------------------------- controls UI

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

export const SIM_LABELS_EN = {
  before: 'Before', after: 'After', play: 'Play', pause: 'Pause', compare: 'Side by side',
  front: 'Front', right: 'Right', left: 'Left', upper: 'Upper', lower: 'Lower', month: 'Month',
  start: 'Today', done: 'Result', drag: 'Drag to rotate', model: '3D model of the teeth',
  unsupported: '3D preview is not available on this device.',
};

// Builds the viewer plus its transport bar inside `host`. Returns { sim, setMonths }.
export function mountSimulator(host, { labels = SIM_LABELS_EN, editable = false, onSelectTooth = null, onInteract = null } = {}) {
  host.classList.add('sim');
  const stage = el('div', 'sim-stage');
  host.appendChild(stage);

  let sim;
  try {
    sim = new OrthoSimulator(stage, { editable, onSelectTooth });
  } catch (error) {
    stage.appendChild(el('p', 'sim-unsupported', labels.unsupported));
    return { sim: null, setMonths() {}, setLabels() {} };
  }
  sim.setView('front', false);
  sim.renderer.domElement.setAttribute('role', 'img');
  sim.renderer.domElement.setAttribute('aria-label', labels.model);

  const tagBefore = el('span', 'sim-tag sim-tag-left', labels.before);
  const tagAfter = el('span', 'sim-tag sim-tag-right', labels.after);
  const hint = el('span', 'sim-hint', labels.drag);
  stage.append(tagBefore, tagAfter, hint);
  tagBefore.hidden = tagAfter.hidden = true;

  const views = el('div', 'sim-views');
  const viewButtons = {};
  for (const name of ['front', 'right', 'left', 'upper', 'lower']) {
    const button = el('button', 'sim-chip', labels[name]);
    button.type = 'button';
    button.addEventListener('click', () => { sim.setView(name); markView(name); interact('view'); });
    viewButtons[name] = button;
    views.appendChild(button);
  }
  const markView = (name) => { for (const [key, button] of Object.entries(viewButtons)) button.classList.toggle('is-on', key === name); };
  markView('front');

  const compareButton = el('button', 'sim-chip sim-chip-toggle', labels.compare);
  compareButton.type = 'button';
  compareButton.addEventListener('click', () => {
    const on = !sim.compare;
    sim.setCompare(on);
    compareButton.classList.toggle('is-on', on);
    tagBefore.hidden = tagAfter.hidden = !on;
    if (on && sim.progress < 0.05) sim.setProgress(1);
    interact('compare');
  });
  views.appendChild(compareButton);
  host.appendChild(views);

  const bar = el('div', 'sim-bar');
  const playButton = el('button', 'sim-play');
  playButton.type = 'button';
  const range = el('input', 'sim-range');
  range.type = 'range'; range.min = '0'; range.max = '1000'; range.value = '0';
  range.setAttribute('aria-label', labels.month);
  const readout = el('span', 'sim-readout');
  bar.append(playButton, range, readout);
  host.appendChild(bar);

  const ends = el('div', 'sim-ends');
  const toBefore = el('button', 'sim-end', labels.before);
  const toAfter = el('button', 'sim-end', labels.after);
  toBefore.type = toAfter.type = 'button';
  toBefore.addEventListener('click', () => { sim.setProgress(0); interact('before'); });
  toAfter.addEventListener('click', () => { sim.setProgress(1); interact('after'); });
  ends.append(toBefore, toAfter);
  host.appendChild(ends);

  let months = 18;
  let interacted = false;
  const interact = (kind) => {
    if (!interacted) { interacted = true; hint.classList.add('is-hidden'); }
    if (onInteract) onInteract(kind);
  };
  stage.addEventListener('pointerdown', () => interact('rotate'), { once: true });

  const refresh = (t, playing) => {
    range.value = String(Math.round(t * 1000));
    range.style.setProperty('--fill', `${t * 100}%`);
    playButton.textContent = playing ? '❚❚' : '▶';
    playButton.setAttribute('aria-label', playing ? labels.pause : labels.play);
    const month = Math.round(t * months);
    readout.textContent = t <= 0.001 ? labels.start : t >= 0.999 ? `${labels.done} · ${labels.month} ${months}` : `${labels.month} ${month} / ${months}`;
    toBefore.classList.toggle('is-on', t <= 0.001);
    toAfter.classList.toggle('is-on', t >= 0.999);
  };
  sim.onProgress(refresh);
  range.addEventListener('input', () => { sim.setProgress(Number(range.value) / 1000); interact('scrub'); });
  playButton.addEventListener('click', () => { if (sim.playing) sim.pause(); else { sim.play(); interact('play'); } });
  refresh(0, false);

  return {
    sim,
    setMonths(value) { months = Math.max(1, Math.round(value) || 1); refresh(sim.progress, sim.playing); },
  };
}
