// Semantic zoom and collision-free annotation placement; independent of the DOM.
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export const smooth = (a, b, x) => {
  const t = clamp((x - a) / (b - a), 0, 1);
  return t * t * (3 - 2 * t);
};
// The selected area (and the path to it) stays open regardless of zoom, so its
// microdistricts never collapse back into one label while it is selected.
export function semanticAreas(model, pixelsPerUnit, selection = null) {
  const result = [],
    focus =
      selection?.kind === "product"
        ? selection.parent
        : selection?.kind === "area"
          ? selection
          : null,
    open = new Set();
  for (let n = focus; n && n.kind === "area"; n = n.parent) open.add(n);
  function visit(node, weight) {
    if (open.has(node)) {
      if (node === focus && !node.children.length) {
        result.push({ node, opacity: weight, detail: 0 });
        return;
      }
      result.push({ node, opacity: weight * 0.2, detail: 1 });
      for (const child of node.children) visit(child, weight);
      return;
    }
    const detail = node.children.length
      ? smooth(480, 650, node.radius * 2 * pixelsPerUnit)
      : 0;
    if (weight * (1 - detail) > 0.015)
      result.push({ node, opacity: weight * (1 - detail), detail });
    for (const child of node.children)
      if (weight * detail > 0.015) visit(child, weight * detail);
  }
  for (const node of model.root.children) visit(node, 1);
  return result;
}
export function compactSize(distance, viewportWidth) {
  const far = smooth(20, 110, distance),
    mobile = viewportWidth < 700;
  return {
    width: Math.round((mobile ? 96 : 102) + far * 10),
    height: Math.round((mobile ? 132 : 138) + far * 10),
    far,
  };
}
export function cardPresentation(
  distance,
  viewportWidth,
  radius,
  height,
  fov = 42,
) {
  const footprint =
    (2 * radius * height) /
    (2 * Math.tan((fov * Math.PI) / 360) * Math.max(1, distance));
  const size = compactSize(distance, viewportWidth);
  return footprint < 70
    ? { ...size, width: 76, height: 34, kind: "price", footprint }
    : { ...size, kind: "card", footprint };
}
export function intersects(a, b, gap = 0) {
  return (
    a.x < b.x + b.width + gap &&
    a.x + a.width + gap > b.x &&
    a.y < b.y + b.height + gap &&
    a.y + a.height + gap > b.y
  );
}
export function placeAnchored(items, bounds, size, obstacles = []) {
  const placed = [],
    width = Math.min(size.width, bounds.width),
    height = Math.min(size.height, bounds.height);
  for (const [index, item] of items.entries()) {
    const options = [];
    for (let row = 0; row < 4; row++)
      for (let col = 0; col < 7; col++) {
        const dx = (col - 3) * (width + 12),
          dy = (row - 1.5) * (height + 12);
        const x = clamp(
          item.anchor.x + dx - width / 2,
          bounds.x,
          bounds.x + bounds.width - width,
        );
        const y = clamp(
          item.anchor.y + dy - height / 2,
          bounds.y,
          bounds.y + bounds.height - height,
        );
        const rect = { x, y, width, height, key: item.key };
        if ([...placed, ...obstacles].some((r) => intersects(rect, r, 10)))
          continue;
        options.push({
          rect,
          score:
            Math.hypot(
              x + width / 2 - item.anchor.x,
              y + height / 2 - item.anchor.y,
            ) +
            Math.abs(col - 3) * 4 +
            index * 0.001,
        });
      }
    options.sort((a, b) => a.score - b.score);
    if (options.length) placed.push(options[0].rect);
  }
  return placed;
}
