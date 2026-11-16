import test from "node:test";
import assert from "node:assert/strict";
import { buildTopology, contextIds } from "../r52/web/js/city-model.js";
import {
  frontier,
  panelSize,
  gridCapacity,
  slotsFor,
  intersects,
} from "../r52/web/js/city-panel-layout.js";
const model = buildTopology([
  { id: 1, title: "RTX 4060" },
  { id: 2, title: "RTX 5060" },
  { id: 3, title: "Куртка" },
]);
test("only one hierarchy frontier is labelled at a time", () => {
  const root = frontier(model.root, model);
  assert(root.every((n) => n.parent === model.root));
  const tech = root.find((n) => n.name === "Техника");
  assert.deepEqual(frontier(tech, model), tech.children);
  const leaf = tech.children[0];
  assert.deepEqual(frontier(leaf, model), [leaf]);
  assert.deepEqual(
    frontier(
      model.products.find((p) => p.parent === leaf),
      model,
    ),
    [leaf],
  );
});
test("empty catalogue has no invented annotations", () =>
  assert.deepEqual(frontier(null, buildTopology([])), []));
test("card size grows on zoom out and stays bounded", () => {
  for (const width of [320, 390, 700, 1280]) {
    let last = panelSize(0, width);
    for (let d = 1; d <= 900; d++) {
      const size = panelSize(d, width);
      assert(size.width >= last.width && size.height >= last.height);
      assert(size.width <= 232 && size.height <= 300);
      last = size;
    }
  }
});
test("rectangle collision detection is symmetric", () => {
  const a = { x: 0, y: 0, width: 10, height: 10 },
    b = { x: 11, y: 0, width: 10, height: 10 };
  assert(!intersects(a, b));
  assert(intersects(a, b, 2));
  assert.equal(intersects(a, b, 2), intersects(b, a, 2));
});
test("layout never overlaps or leaves safe bounds", () => {
  for (const width of [240, 296, 366, 676, 1000, 1232, 1800])
    for (const height of [120, 200, 264, 500, 900])
      for (const distance of [0, 50, 160, 244, 900]) {
        const bounds = { x: 12, y: 210, width, height },
          size = panelSize(distance, width + 24),
          max = panelSize(900, width + 24);
        const slots = slotsFor(bounds, size, max, 40);
        assert.equal(slots.length, gridCapacity(bounds, max).capacity);
        for (const r of slots) {
          assert(r.width > 0 && r.height > 0);
          assert(r.x >= bounds.x - 0.001 && r.y >= bounds.y - 0.001);
          assert(r.x + r.width <= bounds.x + bounds.width + 0.001);
          assert(r.y + r.height <= bounds.y + bounds.height + 0.001);
        }
        for (let i = 0; i < slots.length; i++)
          for (let j = i + 1; j < slots.length; j++)
            assert(!intersects(slots[i], slots[j], 8));
      }
});
test("zoom changes dimensions but not capacity", () => {
  const bounds = { x: 0, y: 0, width: 1232, height: 430 },
    max = panelSize(900, 1280);
  for (const d of [1, 20, 100, 244, 900])
    assert.equal(slotsFor(bounds, panelSize(d, 1280), max, 50).length, 4);
});
test("incomplete last row stays centered", () => {
  const bounds = { x: 0, y: 0, width: 1232, height: 660 },
    max = panelSize(244, 1280),
    slots = slotsFor(bounds, max, max, 5);
  assert.equal(slots.length, 5);
  assert.equal(slots[4].x + slots[4].width / 2, bounds.width / 2);
});
test("small viewport still exposes at least one scrollable card", () => {
  const bounds = { x: 0, y: 0, width: 200, height: 100 },
    max = panelSize(244, 320),
    slots = slotsFor(bounds, max, max, 20);
  assert.equal(slots.length, 1);
  assert.equal(slots[0].height, 100);
});
test("every real offer is addressable beyond the scenery budget", () => {
  const m = buildTopology(
    Array.from({ length: 105 }, (_, i) => ({
      id: i + 1,
      title: "Real " + i,
      category_path: "A > B",
    })),
  );
  assert.equal(m.products.length, 48);
  assert.equal(m.total, 105);
  for (const p of m.root.products) {
    const entity = m.byKey.get("product:" + p.id);
    assert(entity);
    assert.equal(entity.product, p);
    assert.equal(entity.parent.name, "B");
    assert(Number.isFinite(entity.position.x));
  }
  assert.equal(m.byKey.get("product:105").virtual, true);
  assert.deepEqual(contextIds(m.byKey.get("product:105")), [105]);
});
