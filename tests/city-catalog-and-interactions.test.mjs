import test from "node:test";
import assert from "node:assert/strict";
import { CATEGORY_GROUPS, cityPath } from "../r52/web/js/city-taxonomy.js";
import { buildTopology } from "../r52/web/js/city-model.js";
import {
  cardPresentation,
  compactSize,
} from "../r52/web/js/city-spatial-layout.js";
import { canActivateObject } from "../r52/web/js/object-interactions.js";
const options = { includeEmpty: true };
test("planned city exists with zero real products", () => {
  const m = buildTopology([], options);
  assert.equal(m.total, 0);
  assert.equal(m.products.length, 0);
  assert.equal(m.root.children.length, 33);
  assert(m.root.children.every((n) => n.empty && n.planned));
  assert.equal(m.nodes.filter((n) => n.depth === 1).length, 294);
});
test("taxonomy paths are unique", () => {
  assert.equal(
    new Set(CATEGORY_GROUPS.map((g) => g.name)).size,
    CATEGORY_GROUPS.length,
  );
  for (const g of CATEGORY_GROUPS)
    assert.equal(new Set(g.children).size, g.children.length);
});
test("empty areas never generate product IDs", () => {
  const m = buildTopology([], options);
  assert([...m.byKey.keys()].every((k) => !k.startsWith("product:")));
});
test("marketplace names alias into common districts without changing source data", () => {
  const p = {
      id: 1,
      title: "A",
      category_path: "Электроника > Наушники и аудио",
    },
    m = buildTopology([p], options);
  assert.equal(p.category_path, "Электроника > Наушники и аудио");
  assert.equal(m.byKey.get("product:1").parent.parent.name, "Техника");
});
test("top-level-only imports remain reachable in a leaf", () => {
  for (const title of ["Куртка", "Ботинки", "Джинсы"]) {
    const m = buildTopology([{ id: 1, title }], options),
      p = m.byKey.get("product:1");
    assert(p);
    assert(!p.parent.children.length);
    assert.equal(p.parent.products[0].id, 1);
  }
});
test("custom product paths are not dropped by planned taxonomy", () => {
  const m = buildTopology(
    [{ id: 1, title: "A", category_path: "Моя категория > Моя подкатегория" }],
    options,
  );
  assert.equal(m.byKey.get("product:1").parent.name, "Моя подкатегория");
  assert.equal(m.root.children.length, 34);
});
test("category tiles keep coordinates after data and filters change", () => {
  const a = buildTopology([], options),
    b = buildTopology([{ id: 1, title: "RTX 5060" }], options);
  for (const n of a.nodes.filter((n) => n.kind === "area"))
    assert.deepEqual(b.byKey.get(n.key).position, n.position);
});
test("empty district has empty products at every level", () => {
  const m = buildTopology([{ id: 1, title: "RTX" }], options),
    g = m.root.children.find((n) => n.name === "Книги");
  assert.equal(g.products.length, 0);
  assert(g.children.every((n) => n.empty && n.products.length === 0));
});
test("all offers beyond building budget remain addressable with placeholders", () => {
  const m = buildTopology(
    Array.from({ length: 105 }, (_, i) => ({ id: i + 1, title: "RTX " + i })),
    options,
  );
  assert.equal(m.products.length, 48);
  assert.equal(m.total, 105);
  assert(m.byKey.get("product:105"));
  assert.equal(m.byKey.get("product:105").parent.name, "Видеокарты");
});
test("small normal cards have a hard size ceiling independent of distance", () => {
  for (const width of [320, 390, 1280, 2000])
    for (const d of [1, 20, 100, 1000, 10000]) {
      const s = compactSize(d, width);
      assert(s.width <= 112 && s.height <= 148);
    }
});
test("far microdistrict switches to a small clickable price pin", () => {
  const near = cardPresentation(27, 1280, 4.2, 900),
    far = cardPresentation(600, 1280, 4.2, 900);
  assert.equal(near.kind, "card");
  assert.equal(far.kind, "price");
  assert(far.width < near.width && far.height < near.height);
});
test("LOD is relative to real region footprint, not just absolute zoom distance", () => {
  assert.equal(cardPresentation(150, 1280, 18, 900).kind, "card");
  assert.equal(cardPresentation(150, 1280, 1, 900).kind, "price");
});
const root = {},
  event = (extra) => ({ target: { closest: () => null }, button: 0, ...extra });
test("object body opens, nested action control does not", () => {
  assert(canActivateObject(root, event()));
  assert(
    !canActivateObject(
      root,
      event({ target: { closest: () => ({ tagName: "BUTTON" }) } }),
    ),
  );
  assert(canActivateObject(root, event({ target: { closest: () => root } })));
});
test("cancelled, modified and secondary clicks never trigger primary object action", () => {
  for (const flag of [
    "defaultPrevented",
    "ctrlKey",
    "metaKey",
    "altKey",
    "shiftKey",
  ])
    assert(!canActivateObject(root, event({ [flag]: true })));
  assert(!canActivateObject(root, event({ button: 2 })));
});
