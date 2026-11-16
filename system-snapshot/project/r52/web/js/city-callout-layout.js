// R52 2.12: product callouts. Cards line up in two tidy columns beside the
// selected leaf (one shelf on narrow screens) and straight leaders join each card
// to its roof. Pure geometry: the panels, the camera framing and the tests share it.

export const CALLOUT = Object.freeze({
  gap: 10, // between stacked cards
  stub: 16, // straight leader segment that leaves a card
  edge: 14, // air between a column and the city it describes
  minCenter: 320, // the leaf keeps at least this much width between the columns
  perColumn: 4, // cards per column on one page
  shelfMin: 150,
  shelfMax: 260,
  shelfRow: 4,
});

export const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

const finite = (p) => p && Number.isFinite(p.x) && Number.isFinite(p.y);
const order = (a, b) => String(a.key).localeCompare(String(b.key));
const byY = (a, b) => a.anchor.y - b.anchor.y || a.anchor.x - b.anchor.x || order(a, b);
const byX = (a, b) => a.anchor.x - b.anchor.x || a.anchor.y - b.anchor.y || order(a, b);

// Card size and arrangement for the safe screen area (CSS px).
export function calloutMetrics(bounds, kind = "card") {
  const price = kind === "price",
    width = Math.round(clamp(bounds.width * 0.19, 232, 268)),
    columns =
      bounds.width >=
        2 * (width + CALLOUT.stub + CALLOUT.edge) + CALLOUT.minCenter &&
      bounds.height >= 220;
  if (columns)
    return {
      mode: "columns",
      width: price ? 148 : width,
      height: price ? 40 : 76,
      gap: price ? 8 : CALLOUT.gap,
    };
  return { mode: "shelf", width: 0, height: price ? 40 : 72, gap: 8 };
}

// Cards on one page. Always measured with full cards, so zooming out to price
// pins never changes the page.
export function calloutCapacity(bounds, metrics = calloutMetrics(bounds)) {
  if (metrics.mode === "columns") {
    const perColumn = clamp(
      Math.floor((bounds.height + metrics.gap) / (metrics.height + metrics.gap)),
      1,
      CALLOUT.perColumn,
    );
    return { perColumn, capacity: perColumn * 2 };
  }
  const perRow = clamp(
    Math.floor((bounds.width + metrics.gap) / (CALLOUT.shelfMin + metrics.gap)),
    1,
    CALLOUT.shelfRow,
  );
  return { perRow, capacity: perRow };
}

// Proper crossing of segments ab and cd; touching ends do not count.
export function segmentsCross(a, b, c, d) {
  const turn = (p, q, r) => (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x),
    d1 = turn(c, d, a),
    d2 = turn(c, d, b),
    d3 = turn(a, b, c),
    d4 = turn(a, b, d);
  return d1 * d2 < 0 && d3 * d4 < 0;
}

const segments = (card) => [
  [card.port, card.elbow],
  [card.elbow, card.anchor],
];

// Number of leader pairs that cross anywhere.
export function countCrossings(cards) {
  let count = 0;
  for (let i = 0; i < cards.length; i++)
    for (let j = i + 1; j < cards.length; j++)
      if (
        segments(cards[i]).some(([a, b]) =>
          segments(cards[j]).some(([c, d]) => segmentsCross(a, b, c, d)),
        )
      )
        count++;
  return count;
}

// Swapping the roofs of two crossing leaders always shortens the pair, so this
// terminates with a crossing-free assignment of items to fixed slots.
function untangle(slots, items) {
  for (let pass = 0; pass < 64; pass++) {
    let swapped = false;
    for (let i = 0; i < slots.length; i++)
      for (let j = i + 1; j < slots.length; j++)
        if (
          segmentsCross(
            slots[i].elbow,
            items[i].anchor,
            slots[j].elbow,
            items[j].anchor,
          )
        ) {
          [items[i], items[j]] = [items[j], items[i]];
          swapped = true;
        }
    if (!swapped) break;
  }
  return items;
}

function columns(items, bounds, metrics, center) {
  const { perColumn } = calloutCapacity(bounds, metrics),
    list = [...items].sort(byX).slice(0, perColumn * 2),
    side = list.map((item) => (item.anchor.x < center ? 0 : 1)),
    count = (s) => side.filter((x) => x === s).length,
    // Four or more cards are balanced; a column never exceeds its capacity.
    excess = (s) =>
      count(s) > perColumn ||
      (list.length >= 4 && count(s) - count(1 - s) > 1);
  for (let guard = 0; guard < list.length; guard++) {
    const s = excess(0) ? 0 : excess(1) ? 1 : -1;
    if (s < 0) break;
    // The card nearest the middle changes sides: its leader stays shortest.
    let pick = -1;
    list.forEach((item, i) => {
      if (side[i] !== s) return;
      if (
        pick < 0 ||
        (s === 0
          ? item.anchor.x > list[pick].anchor.x
          : item.anchor.x < list[pick].anchor.x)
      )
        pick = i;
    });
    side[pick] = 1 - s;
  }
  const meanY = list.reduce((sum, item) => sum + item.anchor.y, 0) / list.length,
    cards = [];
  for (const s of [0, 1]) {
    const group = list.filter((_, i) => side[i] === s).sort(byY);
    if (!group.length) continue;
    const stack = group.length * metrics.height + (group.length - 1) * metrics.gap,
      top = clamp(
        meanY - stack / 2,
        bounds.y,
        bounds.y + bounds.height - stack,
      ),
      x = s === 0 ? bounds.x : bounds.x + bounds.width - metrics.width,
      slots = group.map((_, j) => {
        const y = top + j * (metrics.height + metrics.gap),
          port = {
            x: s === 0 ? x + metrics.width : x,
            y: y + metrics.height / 2,
          };
        return {
          x,
          y,
          port,
          elbow: {
            x: port.x + (s === 0 ? CALLOUT.stub : -CALLOUT.stub),
            y: port.y,
          },
        };
      });
    untangle(slots, group).forEach((item, j) =>
      cards.push({
        key: item.key,
        x: slots[j].x,
        y: slots[j].y,
        width: metrics.width,
        height: metrics.height,
        side: s === 0 ? "left" : "right",
        port: slots[j].port,
        elbow: slots[j].elbow,
        anchor: { x: item.anchor.x, y: item.anchor.y },
      }),
    );
  }
  return cards;
}

function shelf(items, bounds, metrics) {
  const { perRow } = calloutCapacity(bounds, metrics),
    list = [...items].sort(byX).slice(0, perRow),
    n = list.length,
    width = Math.min(
      CALLOUT.shelfMax,
      Math.floor((bounds.width - (n - 1) * metrics.gap) / n),
    ),
    total = n * width + (n - 1) * metrics.gap,
    left = bounds.x + (bounds.width - total) / 2,
    y = bounds.y + bounds.height - metrics.height,
    slots = list.map((_, j) => {
      const x = left + j * (width + metrics.gap),
        port = { x: x + width / 2, y };
      return { x, y, port, elbow: { x: port.x, y: y - CALLOUT.stub } };
    });
  return untangle(slots, list).map((item, j) => ({
    key: item.key,
    x: slots[j].x,
    y,
    width,
    height: metrics.height,
    side: "bottom",
    port: slots[j].port,
    elbow: slots[j].elbow,
    anchor: { x: item.anchor.x, y: item.anchor.y },
  }));
}

// items: [{ key, anchor: { x, y } }] in screen px; bounds: the safe card area.
export function calloutLayout(items, bounds, options = {}) {
  const metrics = options.metrics || calloutMetrics(bounds, options.kind),
    middle = { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 },
    center = options.center ?? middle.x,
    list = (items || [])
      .filter((item) => item && item.key != null)
      .map((item) => ({
        key: item.key,
        anchor: finite(item.anchor) ? item.anchor : middle,
      }));
  const cards = !list.length
    ? []
    : metrics.mode === "columns"
      ? columns(list, bounds, metrics, center)
      : shelf(list, bounds, metrics);
  return {
    mode: metrics.mode,
    metrics,
    capacity: calloutCapacity(bounds, metrics).capacity,
    cards,
    crossings: countCrossings(cards),
  };
}

// Screen space the camera keeps free for the cards of a leaf.
export function calloutReserve(bounds, count) {
  if (!count) return null;
  const metrics = calloutMetrics(bounds, "card");
  if (metrics.mode === "columns") {
    const side = metrics.width + CALLOUT.stub + CALLOUT.edge;
    return {
      mode: metrics.mode,
      left: bounds.x + side,
      right: bounds.x + bounds.width - side,
      bottom: null,
    };
  }
  return {
    mode: metrics.mode,
    left: null,
    right: null,
    bottom: bounds.y + bounds.height - metrics.height - CALLOUT.stub - CALLOUT.edge,
  };
}

// The viewport between the columns (or above the shelf). It is never squeezed
// into a slit: a too-small result keeps the original viewport.
export function reserveViewport(viewport, reserve) {
  if (!viewport || !reserve) return viewport;
  let { x, width, height } = viewport;
  const { y } = viewport;
  if (reserve.left != null && reserve.right != null) {
    const right = Math.min(x + width, reserve.right);
    x = Math.max(x, reserve.left);
    width = right - x;
  }
  if (reserve.bottom != null) height = Math.min(height, reserve.bottom - y);
  if (width < 200 || height < 160) return viewport;
  return { ...viewport, x, y, width, height };
}

// Inside a leaf every title repeats the leaf's own word ("Видеокарта ..." in
// "Видеокарты"). The card drops it, so six cards no longer read the same.
export function calloutTitle(title, scopeName = "") {
  const text = String(title || "").replace(/\s+/g, " ").trim(),
    words = text.split(" ");
  if (words.length < 3) return text;
  const stems = String(scopeName || "")
    .toLowerCase()
    .split(/[^0-9a-zа-яё]+/)
    .filter((word) => word.length >= 6)
    .map((word) => word.slice(0, Math.max(5, word.length - 2)));
  if (!stems.length) return text;
  const index = words.findIndex(
    (word, i) =>
      i < 3 &&
      stems.some((stem) =>
        word.toLowerCase().replace(/[^0-9a-zа-яё]/g, "").startsWith(stem),
      ),
  );
  if (index < 0) return text;
  const rest = words.filter((_, i) => i !== index);
  return rest.length >= 2 ? rest.join(" ") : text;
}
