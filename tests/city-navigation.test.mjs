import test from "node:test";
import assert from "node:assert/strict";
import {
  MIN_DISTANCE,
  zoomStep,
  wheelInput,
  boundedTarget,
  flightPose,
  flightDuration,
  cityBounds,
} from "../r52/web/js/city-navigation.js";
import { buildTopology } from "../r52/web/js/city-model.js";
import { createUrbanPlan } from "../r52/web/js/urban-layout.js";
import { contains } from "../r52/web/js/city-territories.js";
test("close zoom always stops at a usable minimum", () => {
  let d = 400;
  for (let i = 0; i < 100; i++) d = zoomStep(d, -1);
  assert.equal(d, MIN_DISTANCE);
});
test("three outward steps escape the closest view", () => {
  let d = MIN_DISTANCE;
  for (let i = 0; i < 3; i++) d = zoomStep(d, 1);
  assert(d >= MIN_DISTANCE + 23);
});
test("zoom out also recovers from a legacy zero distance", () =>
  assert(zoomStep(0, 1) >= MIN_DISTANCE));
test("far zoom is bounded", () => {
  let d = 18;
  for (let i = 0; i < 200; i++) d = zoomStep(d, 1, 18, 700);
  assert.equal(d, 700);
});
test("trackpad pixel input stays proportional, not an entire notch", () => {
  assert(wheelInput(1) < 0.01);
  assert.equal(wheelInput(120), 1);
  assert.equal(wheelInput(-120), -1);
});
test("wheel line and page modes are normalized and bounded", () => {
  assert.equal(wheelInput(3, 1), 0.4);
  assert.equal(wheelInput(1, 2, 900), 3);
});
test("pan target is bounded without clamping to one selected district", () => {
  const b = { minX: -100, maxX: 100, minZ: -100, maxZ: 100 };
  assert.deepEqual(boundedTarget({ x: 999, y: -30, z: -999 }, b), {
    x: 118,
    y: 0,
    z: -118,
  });
});
test("flight has exact endpoints and no invalid mid-flight values", () => {
  const a = { x: 0, y: 12, z: 0 },
    b = { x: 100, y: 16, z: -90 };
  assert.deepEqual(flightPose(a, b, 0), a);
  assert.deepEqual(flightPose(a, b, 1), b);
  for (let i = 0; i <= 100; i++) {
    const p = flightPose(a, b, i / 100);
    assert(Object.values(p).every(Number.isFinite));
    assert(p.y >= 12);
  }
});
test("long flights gain clearance rather than dipping through ground", () => {
  const p = flightPose({ x: 0, y: 12, z: 0 }, { x: 100, y: 12, z: 0 }, 0.5, 32);
  assert(p.y >= 32);
});
test("flight duration stays within a comfortable bounded interval", () => {
  assert(flightDuration(0) >= 450);
  assert(flightDuration(10000) <= 1100);
});
test("planned city has a full pedestrian population even without products", () => {
  const m = buildTopology([], { includeEmpty: true }),
    p = createUrbanPlan(m);
  assert.equal(p.walkers.length, 192);
  assert.equal(createUrbanPlan(m, { preview: true }).walkers.length, 32);
  assert(p.streetBlocks.length > 200);
});
test("all pedestrian route corners stay inside the actual city polygon", () => {
  const m = buildTopology([{ id: 1, title: "RTX" }], { includeEmpty: true }),
    p = createUrbanPlan(m);
  for (const w of p.walkers)
    for (const c of w.path) assert(contains(m.root.polygon, c.x, c.z));
});
test("every district has pedestrians rather than only offer districts", () => {
  const m = buildTopology([], { includeEmpty: true }),
    p = createUrbanPlan(m);
  for (const n of m.root.children)
    assert(
      p.walkers.some((w) => w.path.some((c) => contains(n.polygon, c.x, c.z))),
    );
});
