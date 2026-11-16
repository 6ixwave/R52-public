// Pure screen-space layout. Capacity is based on the largest zoom size, so
// zooming out never drops an offer or unexpectedly changes the current page.
export const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
export function frontier(selection, model) {
  const scope =
    selection?.kind === "product" ? selection.parent : selection || model.root;
  return scope.children?.length
    ? scope.children
    : scope.kind === "area"
      ? [scope]
      : [];
}
export function panelSize(distance, viewportWidth) {
  const far = clamp((distance - 24) / 220, 0, 1),
    mobile = viewportWidth < 700;
  return {
    width: Math.round((mobile ? 168 : 202) + (mobile ? 14 : 30) * far),
    height: Math.round((mobile ? 242 : 270) + (mobile ? 22 : 30) * far),
    far,
  };
}
export function intersects(a, b, gap = 0) {
  return (
    a.x < b.x + b.width + gap &&
    a.x + a.width + gap > b.x &&
    a.y < b.y + b.height + gap &&
    a.y + a.height + gap > b.y
  );
}
export function gridCapacity(bounds, maxSize) {
  const gap = 16;
  const cols = clamp(
    Math.floor((bounds.width + gap) / (maxSize.width + gap)),
    1,
    4,
  );
  const rows = clamp(
    Math.floor((bounds.height + gap) / (maxSize.height + gap)),
    1,
    2,
  );
  return { cols, rows, capacity: cols * rows };
}
export function slotsFor(bounds, size, maxSize = size, total = Infinity) {
  const gap = 16,
    grid = gridCapacity(bounds, maxSize);
  const count = Math.min(total, grid.capacity),
    cols = Math.min(grid.cols, Math.max(1, count));
  const rows = Math.ceil(count / cols),
    cellWidth = Math.min(maxSize.width, bounds.width);
  const cellHeight = Math.min(
    maxSize.height,
    Math.max(0, (bounds.height - (rows - 1) * gap) / Math.max(1, rows)),
  );
  const width = Math.min(size.width, cellWidth),
    height = Math.min(size.height, cellHeight);
  const blockHeight = rows * cellHeight + Math.max(0, rows - 1) * gap;
  const y = bounds.y + (bounds.height - blockHeight) * 0.48;
  return Array.from({ length: count }, (_, index) => {
    const row = Math.floor(index / cols),
      n = Math.min(cols, count - row * cols);
    const blockWidth = n * cellWidth + (n - 1) * gap;
    return {
      x:
        bounds.x +
        (bounds.width - blockWidth) / 2 +
        (index % cols) * (cellWidth + gap) +
        (cellWidth - width) / 2,
      y: y + row * (cellHeight + gap) + (cellHeight - height) / 2,
      width,
      height,
    };
  });
}
