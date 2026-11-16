import test from "node:test";
import assert from "node:assert/strict";
import { buildTopology, regionAt } from "../r52/web/js/city-model.js";
import {
  semanticAreas,
  compactSize,
  placeAnchored,
  intersects,
} from "../r52/web/js/city-spatial-layout.js";
const model = buildTopology([
  { id: 1, title: "Real", category_path: "Техника > Видеокарты" },
  { id: 2, title: "Real", category_path: "Техника > Ноутбуки" },
  { id: 3, title: "Real", category_path: "Одежда > Куртки" },
]);
test("distant view labels districts, not subcategories", () => {
  const areas = semanticAreas(model, 5);
  assert.equal(areas.length, 2);
  assert(areas.every((a) => a.node.depth === 0 && a.opacity === 1));
});
test("approaching reveals only actual subcategory names", () => {
  const areas = semanticAreas(model, 25);
  assert.equal(areas.length, 3);
  assert(areas.every((a) => a.node.depth === 1 && a.opacity === 1));
});
test("zoom transition conserves each branch opacity", () => {
  for (let p = 5; p < 25; p += 0.1) {
    const areas = semanticAreas(model, p);
    for (const leaf of model.nodes.filter(
      (n) => n.kind === "area" && !n.children.length,
    )) {
      const path = new Set([leaf.key, leaf.parent.key]);
      const sum = areas
        .filter((a) => path.has(a.node.key))
        .reduce((s, a) => s + a.opacity, 0);
      assert(Math.abs(sum - 1) < 0.016);
    }
  }
});
test("all districts and sibling neighborhoods have distinct colors", () => {
  for (const node of model.nodes.filter((n) => n.children.length > 1)) {
    const colors = node.children.map((n) => n.color);
    assert.equal(new Set(colors).size, colors.length);
  }
});
test("district color lookup uses the deepest real region", () => {
  for (const node of model.nodes.filter(
    (n) => n.kind === "area" && !n.children.length,
  )) {
    assert.equal(regionAt(model, node.position.x, node.position.z), node);
  }
});
test("small cards grow on retreat and shrink on approach", () => {
  for (const w of [390, 1440]) {
    let last = compactSize(0, w);
    for (let d = 1; d < 901; d++) {
      const s = compactSize(d, w);
      assert(s.width >= last.width && s.height >= last.height);
      assert(s.width <= 164 && s.height <= 212);
      last = s;
    }
    assert(compactSize(30, w).width < compactSize(120, w).width);
  }
});
test("six anchored compact cards fit without overlap", () => {
  const bounds = { x: 24, y: 160, width: 1392, height: 600 },
    size = compactSize(900, 1440);
  const placed = placeAnchored(
    Array.from({ length: 6 }, (_, i) => ({
      key: i,
      anchor: { x: 720, y: 450 },
    })),
    bounds,
    size,
  );
  assert.equal(placed.length, 6);
  for (let i = 0; i < placed.length; i++) {
    const r = placed[i];
    assert(
      r.x >= bounds.x &&
        r.y >= bounds.y &&
        r.x + r.width <= bounds.x + bounds.width &&
        r.y + r.height <= bounds.y + bounds.height,
    );
    for (let j = i + 1; j < placed.length; j++)
      assert(!intersects(r, placed[j], 8));
  }
});
test("mobile pair fits within the city viewport", () => {
  const bounds = { x: 12, y: 220, width: 366, height: 470 };
  const placed = placeAnchored(
    [0, 1].map((key) => ({ key, anchor: { x: 195, y: 450 } })),
    bounds,
    compactSize(900, 390),
  );
  assert.equal(placed.length, 2);
  assert(!intersects(...placed, 8));
});
test("empty catalogue has no invented districts", () =>
  assert.deepEqual(semanticAreas(buildTopology([]), 50), []));
