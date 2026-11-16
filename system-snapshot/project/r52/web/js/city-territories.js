// Convex territories form a single continuous map. Boundaries never depend on offers.
export function contains(poly, x, z) {
  if (!poly?.length) return false;
  let sign = 0;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i],
      b = poly[(i + 1) % poly.length],
      cross = (b.x - a.x) * (z - a.z) - (b.z - a.z) * (x - a.x);
    if (Math.abs(cross) < 1e-7) continue;
    const s = Math.sign(cross);
    if (sign && s !== sign) return false;
    sign = s;
  }
  return true;
}
export function clip(poly, nx, nz, k) {
  const out = [];
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i],
      b = poly[(i + 1) % poly.length],
      da = nx * a.x + nz * a.z - k,
      db = nx * b.x + nz * b.z - k;
    const ina = da <= 1e-8,
      inb = db <= 1e-8;
    if (ina) out.push({ ...a });
    if (ina !== inb) {
      const t = da / (da - db);
      out.push({ x: a.x + (b.x - a.x) * t, z: a.z + (b.z - a.z) * t });
    }
  }
  return out.filter((p, i) => {
    const prev = out[(i + out.length - 1) % out.length];
    return Math.hypot(p.x - prev.x, p.z - prev.z) > 1e-7;
  });
}
export function cells(seeds, boundary) {
  return seeds.map((a, i) => {
    let p = boundary.map((v) => ({ ...v }));
    for (let j = 0; j < seeds.length && p.length; j++) {
      if (i === j) continue;
      const b = seeds[j];
      p = clip(
        p,
        b.x - a.x,
        b.z - a.z,
        (b.x * b.x + b.z * b.z - a.x * a.x - a.z * a.z) / 2,
      );
    }
    return p;
  });
}
export function centroid(poly) {
  let area = 0,
    x = 0,
    z = 0;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i],
      b = poly[(i + 1) % poly.length],
      c = a.x * b.z - b.x * a.z;
    area += c;
    x += (a.x + b.x) * c;
    z += (a.z + b.z) * c;
  }
  return Math.abs(area) > 1e-9
    ? { x: x / (3 * area), z: z / (3 * area) }
    : { ...poly[0] };
}
export function innerRadius(poly, p) {
  return Math.min(
    ...poly.map((a, i) => {
      const b = poly[(i + 1) % poly.length];
      return (
        Math.abs((b.x - a.x) * (p.z - a.z) - (b.z - a.z) * (p.x - a.x)) /
        Math.hypot(b.x - a.x, b.z - a.z)
      );
    }),
  );
}
export function bounds(poly) {
  return {
    minX: Math.min(...poly.map((p) => p.x)),
    maxX: Math.max(...poly.map((p) => p.x)),
    minZ: Math.min(...poly.map((p) => p.z)),
    maxZ: Math.max(...poly.map((p) => p.z)),
  };
}
export function createTerritories(root) {
  if (!root.children.length) return;
  const minX = Math.min(...root.children.map((n) => n.position.x - n.radius)),
    maxX = Math.max(...root.children.map((n) => n.position.x + n.radius)),
    minZ = Math.min(...root.children.map((n) => n.position.z - n.radius)),
    maxZ = Math.max(...root.children.map((n) => n.position.z + n.radius));
  root.polygon = [
    { x: minX, z: minZ },
    { x: maxX, z: minZ },
    { x: maxX, z: maxZ },
    { x: minX, z: maxZ },
  ];
  function divide(parent) {
    if (!parent.children.length) return;
    const centre = centroid(parent.polygon),
      r = innerRadius(parent.polygon, centre),
      cols = Math.ceil(Math.sqrt(parent.children.length)),
      rows = Math.ceil(parent.children.length / cols);
    let seeds;
    if (parent === root)
      seeds = parent.children.map((n, i) => ({
        x: n.position.x + (((i * 73) % 17) - 8) * 0.55,
        z: n.position.z + (((i * 41) % 19) - 9) * 0.5,
      }));
    else
      seeds = parent.children.map((n, i) => ({
        x:
          centre.x +
          (((i % cols) - (cols - 1) / 2) * r * 1.2) / Math.max(1, cols - 1),
        z:
          centre.z +
          ((Math.floor(i / cols) - (rows - 1) / 2) * r * 1.2) /
            Math.max(1, rows - 1),
      }));
    const parts = cells(seeds, parent.polygon);
    parent.children.forEach((n, i) => {
      n.polygon = parts[i];
      n.position = centroid(n.polygon);
      n.radius = innerRadius(n.polygon, n.position);
      n.frameRadius = Math.max(
        ...n.polygon.map((p) =>
          Math.hypot(p.x - n.position.x, p.z - n.position.z),
        ),
      );
      divide(n);
    });
  }
  divide(root);
}
