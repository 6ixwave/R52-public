import test from "node:test";
import assert from "node:assert/strict";
import { buildTopology, regionAt } from "../r52/web/js/city-model.js";
import { createUrbanPlan } from "../r52/web/js/urban-layout.js";
import {
  contains,
  cells,
  centroid,
  innerRadius,
} from "../r52/web/js/city-territories.js";
const area = (p) =>
  Math.abs(
    p.reduce((s, a, i) => {
      const b = p[(i + 1) % p.length];
      return s + a.x * b.z - b.x * a.z;
    }, 0) / 2,
  );
test("district polygons exactly cover a single city footprint", () => {
  const m = buildTopology([], { includeEmpty: true });
  assert(
    Math.abs(
      m.root.children.reduce((s, n) => s + area(n.polygon), 0) -
        area(m.root.polygon),
    ) < 1e-6,
  );
  assert(m.root.children.some((n) => n.polygon.length > 4));
  for (let x = -99; x < 113; x += 7.17)
    for (let z = -99; z < 113; z += 6.39)
      assert.equal(
        m.root.children.filter((n) => contains(n.polygon, x, z)).length,
        1,
      );
});
test("subdistricts tessellate their parents without gaps", () => {
  const m = buildTopology([], { includeEmpty: true });
  for (const n of m.nodes.filter((n) => n.children.length && n.polygon))
    assert(
      Math.abs(
        n.children.reduce((s, c) => s + area(c.polygon), 0) - area(n.polygon),
      ) < 1e-6,
    );
});
test("ground labels have fixed interior territory anchors", () => {
  const m = buildTopology([], { includeEmpty: true });
  for (const n of m.nodes.filter((n) => n.kind === "area")) {
    assert(contains(n.polygon, n.position.x, n.position.z));
    assert(n.radius > 0);
    assert.deepEqual(n.position, centroid(n.polygon));
    for (const x of [-n.radius * 0.8, n.radius * 0.8])
      for (const z of [(-n.radius * 0.8) / 3, (n.radius * 0.8) / 3])
        assert(contains(n.polygon, n.position.x + x, n.position.z + z));
  }
});
test("empty districts contain scenery, never manufactured offers", () => {
  const m = buildTopology([], { includeEmpty: true }),
    p = createUrbanPlan(m);
  assert(p.lots.length > 1000);
  assert.equal(m.products.length, 0);
  assert.equal(m.total, 0);
  for (const n of m.root.children) {
    assert(
      p.lots.filter((l) => {
        let q = regionAt(m, l.x, l.z);
        while (q?.parent?.kind === "area") q = q.parent;
        return q === n;
      }).length >= 20,
    );
  }
});
test("real offer anchors remain inside their own territories", () => {
  const m = buildTopology(
    Array.from({ length: 25 }, (_, i) => ({ id: i, title: "RTX " + i })),
    { includeEmpty: true },
  );
  for (const p of m.products)
    assert(contains(p.parent.polygon, p.position.x, p.position.z));
});
test("filtering does not move the city or its lettering anchors", () => {
  const a = buildTopology([{ id: 1, title: "RTX" }], { includeEmpty: true }),
    b = buildTopology([], { includeEmpty: true });
  for (const n of a.nodes.filter((n) => n.kind === "area")) {
    const other = b.byKey.get(n.key);
    assert.deepEqual(n.polygon, other.polygon);
    assert.deepEqual(n.position, other.position);
  }
});
