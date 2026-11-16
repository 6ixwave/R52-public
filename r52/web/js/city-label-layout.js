import { intersects } from "./city-spatial-layout.js";
export function labelAnchor(node) {
  return {
    x: node.position.x,
    y: Math.min(8, Math.max(0.8, node.radius * 0.3)),
    z: node.position.z,
  };
}
export function anchoredLabelRect(point, width, height) {
  return { x: point.x - width / 2, y: point.y - height - 8, width, height };
}
export function chooseLabels(candidates, bounds, obstacles = [], limit = 10) {
  const out = [];
  for (const c of [...candidates].sort(
    (a, b) => b.priority - a.priority || a.order - b.order,
  )) {
    const r = c.rect;
    if (
      r.x < bounds.x ||
      r.y < bounds.y ||
      r.x + r.width > bounds.x + bounds.width ||
      r.y + r.height > bounds.y + bounds.height ||
      [...obstacles, ...out.map((x) => x.rect)].some((o) => intersects(r, o, 18))
    )
      continue;
    out.push(c);
    if(out.length>=limit)break;
  }
  return out;
}
// Label footprint in CSS px; the DOM labels and the camera framing share it.
export function labelSize(node) {
  return {
    width: Math.min(
      node.depth ? 162 : 188,
      Math.max(
        node.depth ? 112 : 124,
        node.name.length * (node.depth ? 6.2 : 7) + 24,
      ),
    ),
    height: node.depth ? 46 : 54,
  };
}
