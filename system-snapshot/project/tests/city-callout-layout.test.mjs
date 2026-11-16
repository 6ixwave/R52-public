import test from "node:test";
import assert from "node:assert/strict";
import {
  CALLOUT,
  calloutMetrics,
  calloutCapacity,
  calloutLayout,
  calloutReserve,
  reserveViewport,
  calloutTitle,
  countCrossings,
  segmentsCross,
} from "../r52/web/js/city-callout-layout.js";

// Safe card areas measured in the app: 1333x948 desktop window and a 390 px phone.
const desktop = { x: 24, y: 170, width: 1285, height: 624 };
const laptop = { x: 24, y: 150, width: 1052, height: 520 };
const phone = { x: 12, y: 150, width: 366, height: 520 };

const overlap = (a, b) =>
  a.x < b.x + b.width &&
  b.x < a.x + a.width &&
  a.y < b.y + b.height &&
  b.y < a.y + a.height;
const inside = (r, b) =>
  r.x >= b.x - 0.01 &&
  r.y >= b.y - 0.01 &&
  r.x + r.width <= b.x + b.width + 0.01 &&
  r.y + r.height <= b.y + b.height + 0.01;

function rng(seed) {
  let s = seed >>> 0;
  return () => (s = (s * 1664525 + 1013904223) >>> 0) / 4294967296;
}

// Roofs of one leaf: anchors inside the viewport the camera keeps for the leaf.
function roofs(n, bounds, random, spread = 1) {
  const free = reserveViewport(
    { x: 0, y: bounds.y, width: bounds.x * 2 + bounds.width, height: bounds.height },
    calloutReserve(bounds, n),
  );
  const cx = free.x + free.width / 2,
    cy = free.y + free.height / 2;
  return Array.from({ length: n }, (_, i) => ({
    key: 1000 + i,
    anchor: {
      x: cx + (random() - 0.5) * free.width * spread,
      y: cy + (random() - 0.5) * free.height * spread,
    },
  }));
}

function checkLayout(result, items, bounds) {
  assert.equal(result.cards.length, Math.min(items.length, result.capacity));
  for (const card of result.cards)
    assert.ok(inside(card, bounds), `outside: ${JSON.stringify(card)}`);
  for (let i = 0; i < result.cards.length; i++)
    for (let j = i + 1; j < result.cards.length; j++)
      assert.ok(!overlap(result.cards[i], result.cards[j]), "cards overlap");
  assert.equal(result.crossings, 0, `leaders cross: ${JSON.stringify(items)}`);
  assert.equal(countCrossings(result.cards), 0);
}

test("wide screens get two columns, narrow screens one shelf", () => {
  assert.equal(calloutMetrics(desktop).mode, "columns");
  assert.equal(calloutMetrics(laptop).mode, "columns");
  assert.equal(calloutMetrics({ x: 24, y: 120, width: 852, height: 560 }).mode, "columns");
  assert.equal(calloutMetrics({ x: 24, y: 120, width: 780, height: 560 }).mode, "shelf");
  assert.equal(calloutMetrics(phone).mode, "shelf");
  assert.equal(calloutMetrics(desktop).width, 244);
  assert.equal(calloutMetrics(desktop, "price").height, 40);
  assert.deepEqual(calloutCapacity(desktop, calloutMetrics(desktop)), {
    perColumn: CALLOUT.perColumn,
    capacity: CALLOUT.perColumn * 2,
  });
  assert.equal(calloutCapacity(phone, calloutMetrics(phone)).capacity, 2);
  // A short window keeps at least one card per column.
  assert.equal(
    calloutCapacity({ x: 24, y: 100, width: 1200, height: 230 }).perColumn,
    2,
  );
});

test("columns never overlap, stay inside, balance and never cross", () => {
  const random = rng(52);
  for (const bounds of [desktop, laptop])
    for (let trial = 0; trial < 400; trial++) {
      const n = 1 + Math.floor(random() * 8),
        items = roofs(n, bounds, random, 0.15 + random() * 0.85),
        result = calloutLayout(items, bounds);
      assert.equal(result.mode, "columns");
      checkLayout(result, items, bounds);
      const left = result.cards.filter((c) => c.side === "left").length,
        right = n - left;
      assert.ok(left <= CALLOUT.perColumn && right <= CALLOUT.perColumn);
      if (n >= 4) assert.ok(Math.abs(left - right) <= 1, `${left}/${right}`);
      // Deterministic and independent of the input order.
      assert.deepEqual(calloutLayout(items, bounds), result);
      const sorted = (r) =>
        [...r.cards].sort((a, b) => a.key - b.key).map((c) => [c.key, c.x, c.y]);
      assert.deepEqual(sorted(calloutLayout([...items].reverse(), bounds)), sorted(result));
    }
});

test("each column is one contiguous stack on a shared axis", () => {
  const items = [
    { key: 1, anchor: { x: 560, y: 430 } },
    { key: 2, anchor: { x: 610, y: 520 } },
    { key: 3, anchor: { x: 590, y: 470 } },
    { key: 4, anchor: { x: 720, y: 450 } },
    { key: 5, anchor: { x: 760, y: 500 } },
    { key: 6, anchor: { x: 700, y: 540 } },
  ];
  const { cards } = calloutLayout(items, desktop);
  const tops = {};
  for (const side of ["left", "right"]) {
    const column = cards.filter((c) => c.side === side).sort((a, b) => a.y - b.y);
    assert.equal(column.length, 3);
    column.forEach((card, i) => {
      assert.equal(card.x, column[0].x);
      if (i) assert.equal(card.y - column[i - 1].y, card.height + CALLOUT.gap);
    });
    tops[side] = column[0].y;
  }
  assert.equal(tops.left, tops.right);
  // Leaders leave the inner edge with a short straight stub.
  for (const card of cards) {
    assert.equal(card.port.y, card.y + card.height / 2);
    if (card.side === "left") {
      assert.equal(card.x, desktop.x);
      assert.equal(card.port.x, card.x + card.width);
      assert.equal(card.elbow.x, card.port.x + CALLOUT.stub);
    } else {
      assert.equal(card.x + card.width, desktop.x + desktop.width);
      assert.equal(card.port.x, card.x);
      assert.equal(card.elbow.x, card.port.x - CALLOUT.stub);
    }
  }
  // The left anchors go to the left column, ordered top to bottom.
  assert.deepEqual(
    cards.filter((c) => c.side === "left").sort((a, b) => a.y - b.y).map((c) => c.key),
    [1, 3, 2],
  );
});

test("a stack never leaves the safe area", () => {
  const items = [1, 2, 3, 4].map((key) => ({ key, anchor: { x: 500, y: 175 + key } }));
  const { cards } = calloutLayout(items, desktop);
  for (const card of cards) assert.ok(inside(card, desktop));
  assert.equal(Math.min(...cards.map((c) => c.y)), desktop.y);
});

test("narrow screens use one centred shelf at the bottom", () => {
  const random = rng(7);
  for (let trial = 0; trial < 300; trial++) {
    const n = 1 + Math.floor(random() * 2),
      items = roofs(n, phone, random, 0.2 + random() * 0.8),
      result = calloutLayout(items, phone);
    assert.equal(result.mode, "shelf");
    checkLayout(result, items, phone);
    for (const card of result.cards) {
      assert.equal(card.side, "bottom");
      assert.equal(card.y + card.height, phone.y + phone.height);
      assert.equal(card.port.y, card.y);
      assert.equal(card.elbow.y, card.y - CALLOUT.stub);
    }
    const left = Math.min(...result.cards.map((c) => c.x)) - phone.x,
      right = phone.x + phone.width - Math.max(...result.cards.map((c) => c.x + c.width));
    assert.ok(Math.abs(left - right) < 1);
  }
  const wide = { x: 24, y: 150, width: 760, height: 520 },
    result = calloutLayout(roofs(4, wide, rng(3)), wide);
  assert.equal(result.cards.length, 4);
  assert.ok(result.cards.every((c) => c.width <= CALLOUT.shelfMax));
});

test("the camera keeps the leaf between the columns", () => {
  const reserve = calloutReserve(desktop, 6),
    viewport = { x: 0, y: 140, width: 1333, height: 720 },
    free = reserveViewport(viewport, reserve),
    side = calloutMetrics(desktop).width + CALLOUT.stub + CALLOUT.edge;
  assert.equal(free.x, desktop.x + side);
  assert.equal(free.x + free.width, desktop.x + desktop.width - side);
  assert.equal(free.y, viewport.y);
  assert.equal(free.height, viewport.height);
  const { cards } = calloutLayout(roofs(6, desktop, rng(11)), desktop);
  for (const card of cards)
    assert.ok(card.x + card.width <= free.x || card.x >= free.x + free.width);
  const shelf = reserveViewport(
    { x: 0, y: 100, width: 390, height: 720 },
    calloutReserve(phone, 2),
  );
  assert.ok(shelf.y + shelf.height <= phone.y + phone.height - 72 - CALLOUT.stub);
  // Never a slit: a viewport that would become too thin stays as it was.
  const thin = { x: 900, y: 100, width: 300, height: 600 };
  assert.equal(reserveViewport(thin, reserve), thin);
  assert.equal(calloutReserve(desktop, 0), null);
  assert.equal(reserveViewport(viewport, null), viewport);
});

test("invalid anchors still give a tidy layout", () => {
  const items = [
    { key: "a", anchor: { x: NaN, y: 10 } },
    { key: "b", anchor: null },
    { key: "c", anchor: { x: 600, y: 400 } },
    null,
  ];
  const result = calloutLayout(items, desktop);
  assert.equal(result.cards.length, 3);
  for (const card of result.cards) assert.ok(inside(card, desktop));
  assert.deepEqual(calloutLayout([], desktop).cards, []);
});

test("segment crossings ignore touching ends", () => {
  const p = (x, y) => ({ x, y });
  assert.equal(segmentsCross(p(0, 0), p(10, 10), p(0, 10), p(10, 0)), true);
  assert.equal(segmentsCross(p(0, 0), p(10, 0), p(10, 0), p(20, 5)), false);
  assert.equal(segmentsCross(p(0, 0), p(10, 0), p(0, 5), p(10, 5)), false);
  const crossing = [
    { port: p(0, 0), elbow: p(10, 0), anchor: p(100, 100) },
    { port: p(0, 100), elbow: p(10, 100), anchor: p(100, 0) },
  ];
  assert.equal(countCrossings(crossing), 1);
});

test("titles drop the word their leaf already says", () => {
  assert.equal(
    calloutTitle("Palit Видеокарта GeForce RTX 5070 Ti GamingPro 12 ГБ", "Видеокарты"),
    "Palit GeForce RTX 5070 Ti GamingPro 12 ГБ",
  );
  assert.equal(calloutTitle("Видеокарта Тестовая RTX 4060 8 ГБ", "Видеокарты"), "Тестовая RTX 4060 8 ГБ");
  assert.equal(calloutTitle("Монитор игровой 27 дюймов", "Мониторы"), "игровой 27 дюймов");
  assert.equal(calloutTitle("Смартфон Тестовый 256 ГБ", "Видеокарты"), "Смартфон Тестовый 256 ГБ");
  // Too short to shorten; short leaf names are never used as stems.
  assert.equal(calloutTitle("Видеокарта RTX", "Видеокарты"), "Видеокарта RTX");
  assert.equal(calloutTitle("Другая модель телефона", "Другое"), "Другая модель телефона");
  assert.equal(calloutTitle("  Джинсы   синие ", ""), "Джинсы синие");
});
