// Automatic camera framing. The whole selection (ground, microdistricts, their
// labels and buildings) is fitted into the part of the screen that the
// interface leaves free, instead of flying to a fixed distance from the centre.
import * as THREE from "./vendor/three.module.js";
import { labelAnchor, labelSize } from "./city-label-layout.js";
import {
  boundedTarget,
  MIN_DISTANCE,
  MIN_ALTITUDE,
} from "./city-navigation.js";

// Comfortable oblique views; whole-city framing uses a lower 32° elevation.
export const FRAME_ELEVATION = 0.70;
export const CITY_ELEVATION = 0.53;
const LABEL_GAP = 8;

function square(node) {
  const x = node.position?.x || 0,
    z = node.position?.z || 0,
    r = node.radius || 18;
  return [
    { x: x - r, z: z - r },
    { x: x + r, z: z - r },
    { x: x + r, z: z + r },
    { x: x - r, z: z + r },
  ];
}

// World points with screen paddings (CSS px) that must stay inside the viewport.
export function selectionPoints(
  node,
  { productEntity = (p) => p, labels = true } = {},
) {
  const points = [],
    add = (x, y, z, pad) =>
      points.push({ x, y, z, left: 0, right: 0, top: 0, bottom: 0, ...pad });
  if (!node) return points;
  if (node.kind === "product") {
    const r = Math.max(2, (node.step || 3) / 2),
      top = Math.max(1, node.roofHeight || 5);
    for (const dx of [-r, r])
      for (const dz of [-r, r])
        for (const y of [0, top])
          add(node.position.x + dx, y, node.position.z + dz, { key: node.key });
    return points;
  }
  const children = node.children || [],
    leaf = node.kind === "area" && !children.length;
  for (const p of node.polygon?.length ? node.polygon : square(node))
    add(p.x, 0, p.z);
  for (const child of children)
    for (const p of child.polygon?.length ? child.polygon : square(child))
      add(p.x, 0, p.z);
  // The labels this view shows have to fit too, not only the ground.
  if (labels)
    for (const labelled of children.length
      ? children
      : node.kind === "root"
        ? []
        : [node]) {
      const a = labelAnchor(labelled),
        s = labelSize(labelled);
      add(a.x, a.y, a.z, {
        left: s.width / 2 + LABEL_GAP,
        right: s.width / 2 + LABEL_GAP,
        top: s.height + 8 + LABEL_GAP,
        bottom: LABEL_GAP,
        key: labelled.key,
      });
    }
  // Real buildings: roofs must not be cut by the top edge; a leaf also keeps
  // room for the compact price cards around them.
  const seen = new Set();
  for (const record of node.products || []) {
    const p = productEntity(record);
    if (!p?.position || seen.has(p)) continue;
    seen.add(p);
    add(
      p.position.x,
      p.roofHeight || 5,
      p.position.z,
      leaf
        ? { left: 28, right: 28, top: 24, bottom: 16, key: p.key }
        : { key: p.key },
    );
  }
  return points;
}

export function frameDirection(yaw, elevation = FRAME_ELEVATION) {
  const c = Math.sqrt(Math.max(0, 1 - elevation * elevation));
  return new THREE.Vector3(Math.sin(yaw) * c, elevation, Math.cos(yaw) * c);
}

export function projectedFit(
  points,
  camera,
  width,
  height,
  viewport,
  tolerance = 0.5,
) {
  camera.updateMatrixWorld();
  const v = new THREE.Vector3();
  let inside = points.length > 0;
  const projected = points.map((p) => {
    v.set(p.x, p.y, p.z).project(camera);
    const x = ((v.x + 1) * width) / 2,
      y = ((1 - v.y) * height) / 2,
      ok =
        v.z > -1 &&
        v.z < 1 &&
        x - p.left >= viewport.x - tolerance &&
        x + p.right <= viewport.x + viewport.width + tolerance &&
        y - p.top >= viewport.y - tolerance &&
        y + p.bottom <= viewport.y + viewport.height + tolerance;
    if (!ok) inside = false;
    return { key: p.key || null, x, y, inside: ok };
  });
  return { inside, projected };
}

// Exact fit for a fixed view direction. In camera space a point stays inside
// its allowed screen interval iff the camera offset satisfies a pair of linear
// inequalities, so the smallest feasible depth is found in closed form; then
// the camera backs off only as far as the distance limit and roofs require.
export function fitCamera({
  node,
  camera,
  width,
  height,
  viewport,
  bounds = null,
  yaw = 0.6,
  elevation = FRAME_ELEVATION,
  productEntity = (p) => p,
  minDistance = MIN_DISTANCE * 1.5,
  floorAt = () => MIN_ALTITUDE,
  points = selectionPoints(node, { productEntity, labels: node?.kind!=="root" }),
}) {
  if (!points.length || !width || !height || !viewport) return null;
  const normal = frameDirection(yaw, elevation),
    probe = new THREE.PerspectiveCamera(
      camera.fov,
      width / height,
      camera.near,
      camera.far,
    );
  probe.position.copy(normal);
  probe.lookAt(0, 0, 0);
  probe.updateMatrixWorld();
  const toLocal = probe.quaternion.clone().invert(),
    ty = Math.tan((probe.fov * Math.PI) / 360),
    sx = 1 / (probe.aspect * ty),
    sy = 1 / ty,
    targetY =
      node?.kind === "product"
        ? Math.min(12, Math.max(1, node.roofHeight || 5) * 0.5)
        : 0;
  const rows = points.map((p) => {
    const q = new THREE.Vector3(p.x, p.y, p.z).applyQuaternion(toLocal);
    let x0 = (2 * (viewport.x + p.left)) / width - 1,
      x1 = (2 * (viewport.x + viewport.width - p.right)) / width - 1,
      y0 = 1 - (2 * (viewport.y + viewport.height - p.bottom)) / height,
      y1 = 1 - (2 * (viewport.y + p.top)) / height;
    if (x0 > x1) x0 = x1 = (x0 + x1) / 2;
    if (y0 > y1) y0 = y1 = (y0 + y1) / 2;
    return { q, x: [x0 / sx, x1 / sx], y: [y0 / sy, y1 / sy] };
  });
  // Points with equal paddings share one constraint pair; only extremes matter.
  const groups = (axis) => {
    const map = new Map();
    for (const r of rows) {
      const [a, b] = r[axis],
        v = r.q[axis],
        z = r.q.z,
        key = a + "|" + b;
      let g = map.get(key);
      if (!g) map.set(key, (g = { a, b, low: -Infinity, high: Infinity }));
      g.low = Math.max(g.low, v + b * z);
      g.high = Math.min(g.high, v + a * z);
    }
    return [...map.values()];
  };
  const gx = groups("x"),
    gy = groups("y");
  let depth = -Infinity;
  for (const r of rows) depth = Math.max(depth, r.q.z + 1);
  for (const list of [gx, gy])
    for (const i of list)
      for (const j of list) {
        const den = i.b - j.a;
        if (den > 1e-9) depth = Math.max(depth, (i.low - j.high) / den);
      }
  const centre = (list, z) => {
    let lo = -Infinity,
      hi = Infinity;
    for (const g of list) {
      lo = Math.max(lo, g.low - g.b * z);
      hi = Math.min(hi, g.high - g.a * z);
    }
    return Number.isFinite(lo + hi) ? (lo + hi) / 2 : 0;
  };
  const pose = (z) => {
    const position = new THREE.Vector3(
        centre(gx, z),
        centre(gy, z),
        z,
      ).applyQuaternion(probe.quaternion),
      distance = (position.y - targetY) / elevation;
    return {
      position,
      target: position.clone().addScaledVector(normal, -distance),
      distance,
    };
  };
  let fit = pose(depth);
  for (let step = 0; step < 8; step++) {
    const need = Math.max(
      minDistance - fit.distance,
      (floorAt(fit.position.x, fit.position.z) - fit.position.y) / elevation,
    );
    if (!(need > 1e-3)) break;
    depth += need + 0.05;
    fit = pose(depth);
  }
  if (bounds) {
    const b = boundedTarget(fit.target, bounds),
      shift = new THREE.Vector3(
        b.x - fit.target.x,
        b.y - fit.target.y,
        b.z - fit.target.z,
      );
    fit.target.add(shift);
    fit.position.add(shift);
  }
  // Narrow screens can require a distance beyond the original clipping plane.
  probe.far=Math.max(probe.far,fit.distance+Math.max(...points.map(p=>Math.hypot(p.x-fit.target.x,p.y-fit.target.y,p.z-fit.target.z)))+100);
  probe.position.copy(fit.position);
  probe.lookAt(fit.target);
  probe.updateProjectionMatrix();
  let check = projectedFit(points, probe, width, height, viewport);
  // A navigation-bound target correction can shift the exact unconstrained fit.
  for(let retry=0;!check.inside&&retry<16;retry++){
    depth+=Math.max(1,depth*.06);fit=pose(depth);
    if(bounds){const b=boundedTarget(fit.target,bounds),shift=new THREE.Vector3(b.x-fit.target.x,b.y-fit.target.y,b.z-fit.target.z);fit.target.add(shift);fit.position.add(shift);}
    probe.far=Math.max(probe.far,fit.distance+Math.max(...points.map(p=>Math.hypot(p.x-fit.target.x,p.y-fit.target.y,p.z-fit.target.z)))+100);
    probe.position.copy(fit.position);probe.lookAt(fit.target);probe.updateProjectionMatrix();check=projectedFit(points,probe,width,height,viewport);
  }
  return {
    ...fit,
    far:probe.far,
    points,
    viewport,
    complete: check.inside,
    projected: check.projected,
  };
}
