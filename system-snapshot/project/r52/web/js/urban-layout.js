import { contains } from "./city-territories.js";
// R52: a deterministic city, not generated marketplace offers.
// Noise/relief adapted from React Bits Topography, Copyright (c) 2026 David Haz.
// MIT + Commons Clause; see vendor/REACT-BITS-LICENSE.txt. Heights become buildings, never hills.
export const CITY_COLOR = "#822dd8";
export function lattice(x, y) {
  let h =
    (Math.imul(x + 1073741824, 2654435761) +
      Math.imul(y + 1073741824, 2246822519)) >>>
    0;
  h = Math.imul(h ^ (h >>> 15), 739982445) >>> 0;
  h = Math.imul(h ^ (h >>> 12), 695872825) >>> 0;
  return (h ^ (h >>> 15)) >>> 0;
}
const corner = (x, y, fx, fy) => {
  const h = lattice(x, y);
  return ((h & 65535) / 32767.5 - 1) * fx + ((h >>> 16) / 32767.5 - 1) * fy;
};
export function noise(x, y) {
  const ix = Math.floor(x),
    iy = Math.floor(y),
    fx = x - ix,
    fy = y - iy,
    ux = fx * fx * fx * (fx * (fx * 6 - 15) + 10),
    uy = fy * fy * fy * (fy * (fy * 6 - 15) + 10),
    a = corner(ix, iy, fx, fy),
    b = corner(ix + 1, iy, fx - 1, fy),
    d = corner(ix, iy + 1, fx, fy - 1),
    e = corner(ix + 1, iy + 1, fx - 1, fy - 1),
    top = a + (b - a) * ux;
  return top + (d + (e - d) * ux - top) * uy;
}
export function relief(tx, tz) {
  let x = tx * 0.045,
    z = tz * 0.045;
  const wx = noise(x * 0.42 + 0.6, z * 0.42 + 0.2),
    wz = noise(x * 0.42 + 5, z * 0.42 + 1.3);
  x += wx * 0.8;
  z += wz * 0.8;
  let n = noise(x, z) * 0.55;
  const qx = 1.6 * x - 1.2 * z + 7.1,
    qz = 1.2 * x + 1.6 * z + 3.7;
  n +=
    noise(qx, qz) * 0.15 +
    noise(1.6 * qx - 1.2 * qz + 1.9, 1.2 * qx + 1.6 * qz + 8.3) * 0.04;
  const s = Math.log(1 + Math.exp(7 * (3 * n + 0.05))) / 7;
  return s * s * 1.4 + 0.1 * n;
}
export function createUrbanPlan(model, { preview = false } = {}) {
  const blocks = Math.min(
      model.includeEmpty ? 21 : 17,
      Math.max(9, Math.ceil((model.root.radius * 2) / 12) | 1),
    ),
    half = blocks * 6,
    lots = [],
    parks = [],
    walkers = [],
    streetBlocks = [];
  const allowed = (p) =>
    model.root.polygon
      ? contains(model.root.polygon, p.x, p.z)
      : !model.root.children.length ||
        model.root.children.some(
          (n) =>
            Math.abs(p.x - n.position.x) <= n.radius + 3 &&
            Math.abs(p.z - n.position.z) <= n.radius + 3,
        );
  const pathAt = (x, z) => [
    { x: x - 5.15, z: z - 5.15 },
    { x: x + 5.15, z: z - 5.15 },
    { x: x + 5.15, z: z + 5.15 },
    { x: x - 5.15, z: z + 5.15 },
  ];
  for (let ix = 0; ix < blocks; ix++)
    for (let iz = 0; iz < blocks; iz++) {
      const cx = (ix - (blocks - 1) / 2) * 12,
        cz = (iz - (blocks - 1) / 2) * 12,
        seed = lattice(ix + 77, iz + 31),
        park =
          seed % 13 === 0 &&
          Math.hypot(cx, cz) > 15 &&
          !model.products.some(
            (p) => Math.hypot(p.position.x - cx, p.position.z - cz) < 8,
          );
      if (model.includeEmpty) {
        const roots = model.root.children;
        const minX = Math.min(
            ...roots.map((n) =>
              n.polygon
                ? Math.min(...n.polygon.map((p) => p.x))
                : n.position.x - n.radius,
            ),
          ),
          maxX = Math.max(
            ...roots.map((n) =>
              n.polygon
                ? Math.max(...n.polygon.map((p) => p.x))
                : n.position.x + n.radius,
            ),
          ),
          minZ = Math.min(
            ...roots.map((n) =>
              n.polygon
                ? Math.min(...n.polygon.map((p) => p.z))
                : n.position.z - n.radius,
            ),
          ),
          maxZ = Math.max(
            ...roots.map((n) =>
              n.polygon
                ? Math.max(...n.polygon.map((p) => p.z))
                : n.position.z + n.radius,
            ),
          );
        if (
          cx - 6 < minX - 6 ||
          cx + 6 > maxX + 6 ||
          cz - 6 < minZ - 6 ||
          cz + 6 > maxZ + 6
        )
          continue;
      }
      if (pathAt(cx, cz).every(allowed)) streetBlocks.push({ x: cx, z: cz });
      if (park) {
        parks.push({ x: cx, z: cz, seed });
        continue;
      }
      for (let a = -1; a <= 1; a += 2)
        for (let b = -1; b <= 1; b += 2) {
          const x = cx + a * 2.6,
            z = cz + b * 2.6;
          if (
            !model.includeEmpty &&
            model.root.children.length &&
            !model.root.children.some(
              (n) =>
                Math.abs(x - n.position.x) < n.radius &&
                Math.abs(z - n.position.z) < n.radius,
            )
          )
            continue;
          const s = lattice(ix * 4 + a, iz * 4 + b),
            reserved = model.products.some(
              (p) =>
                Math.hypot(p.position.x - x, p.position.z - z) <
                Math.min(
                  6,
                  Math.max(2.8, Math.min(3.4, p.step * 0.43) / 2 + 1.9),
                ),
            );
          if (reserved) continue;
          const centre = Math.max(0, 1 - Math.hypot(x, z) / (half * 0.9)),
            field = Math.max(0, relief(x, z));
          const height = Math.min(
            23,
            2.1 +
              Math.pow(centre, 1.5) * 12 +
              field * 7 +
              ((s % 100) / 100) * 4,
          );
          lots.push({
            x,
            z,
            width: 3.0 + (s % 11) / 10,
            depth: 2.8 + ((s >>> 5) % 13) / 10,
            height,
            seed: s,
          });
        }
    }
  const people = preview ? 32 : 192;
  for (let i = 0; i < people && streetBlocks.length; i++) {
    const seed = lattice(i + 209, 74),
      offer =
        i < 36 && model.products.length
          ? model.products[i % model.products.length]
          : null,
      area = model.root.children[i % Math.max(1, model.root.children.length)],
      focus = offer?.position || area?.position || { x: 0, z: 0 };
    const ranked = streetBlocks.filter(
      (b) =>
        !area || offer || !area.polygon || contains(area.polygon, b.x, b.z),
    );
    const choices = (ranked.length ? ranked : streetBlocks)
      .map((b) => ({ b, d: Math.hypot(b.x - focus.x, b.z - focus.z) }))
      .sort((a, b) => a.d - b.d || a.b.x - b.b.x || a.b.z - b.b.z);
    const choice =
        choices[(seed >>> 8) % Math.min(offer ? 3 : 8, choices.length)].b,
      cx = choice.x,
      cz = choice.z;
    walkers.push({
      id: i,
      seed,
      speed: 0.65 + (seed % 80) / 100,
      progress: (seed % 100) / 100,
      edge: (seed >>> 11) % 4,
      direction: seed % 2 ? 1 : -1,
      pause: 0,
      phase: (seed % 628) / 100,
      path: [
        { x: cx - 5.15, z: cz - 5.15 },
        { x: cx + 5.15, z: cz - 5.15 },
        { x: cx + 5.15, z: cz + 5.15 },
        { x: cx - 5.15, z: cz + 5.15 },
      ],
    });
  }
  return {
    blocks,
    half,
    lots,
    parks: parks.filter(
      (p) =>
        !model.root.children.length ||
        model.root.children.some(
          (n) =>
            Math.abs(p.x - n.position.x) <= n.radius &&
            Math.abs(p.z - n.position.z) <= n.radius,
        ),
    ),
    walkers: walkers.filter((w) => w.path.every(allowed)),
    streetBlocks,
  };
}
export function advanceWalker(w, dt) {
  if (w.pause > 0) {
    w.pause = Math.max(0, w.pause - dt);
    return walkerPose(w);
  }
  const a = w.path[w.edge],
    next = (w.edge + w.direction + 4) % 4,
    b = w.path[next],
    length = Math.hypot(b.x - a.x, b.z - a.z);
  w.progress += (Math.min(0.06, Math.max(0, dt)) * w.speed) / length;
  if (w.progress >= 1) {
    w.progress -= 1;
    w.edge = next;
    if ((w.edge + w.id) % 3 === 0) w.pause = 0.5 + (w.seed % 100) / 100;
  }
  return walkerPose(w);
}
export function walkerPose(w) {
  const a = w.path[w.edge],
    b = w.path[(w.edge + w.direction + 4) % 4],
    t = w.progress;
  return {
    x: a.x + (b.x - a.x) * t,
    z: a.z + (b.z - a.z) * t,
    yaw: Math.atan2(b.x - a.x, b.z - a.z),
    walking: w.pause <= 0,
  };
}
