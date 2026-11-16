import test from "node:test";
import assert from "node:assert/strict";
import * as THREE from "../r52/web/js/vendor/three.module.js";
import { buildTopology } from "../r52/web/js/city-model.js";
import {
  fitCamera,
  projectedFit,
  selectionPoints,
  FRAME_ELEVATION,
} from "../r52/web/js/city-camera-fit.js";
import { semanticAreas } from "../r52/web/js/city-spatial-layout.js";
import {
  cityBounds,
  dampDistance,
  flightEase,
  flightPose,
  MIN_DISTANCE,
} from "../r52/web/js/city-navigation.js";

const products = [
  ...Array.from({ length: 10 }, (_, i) => ({
    id: i + 1,
    title: `RTX 40${60 + i} видеокарта`,
  })),
  { id: 20, title: "Куртка зимняя", category_path: "Одежда" },
];
const model = buildTopology(products, { includeEmpty: true }),
  bounds = cityBounds(model),
  entity = (p) => model.byKey.get("product:" + p.id) || p;

// Free part of the screen under the header/toolbar and above the footer.
function view(w, h) {
  const phone = w < 700;
  return phone
    ? { x: 12, y: 214, width: w - 24, height: h - 214 - 195 }
    : { x: 24, y: 152, width: w - 48, height: h - 152 - 106 };
}
function fitted(node, w, h, yaw = 0.6) {
  const camera = new THREE.PerspectiveCamera(42, w / h, 0.05, 1600),
    viewport = view(w, h),
    fit = fitCamera({
      node,
      camera,
      width: w,
      height: h,
      viewport,
      bounds,
      yaw,
      productEntity: entity,
    });
  camera.position.copy(fit.position);
  camera.lookAt(fit.target);
  camera.updateProjectionMatrix();
  return {
    fit,
    camera,
    viewport,
    check: projectedFit(fit.points, camera, w, h, viewport),
  };
}
const districts = model.root.children,
  largest = [...districts].sort((a, b) => b.children.length - a.children.length)[0];

for (const [label, w, h] of [
  ["desktop", 1440, 900],
  ["tablet", 1024, 768],
  ["phone", 390, 844],
])
  test(`every district and all its microdistricts fit on ${label}`, () => {
    for (const node of districts) {
      const { fit, check } = fitted(node, w, h);
      assert.ok(fit.complete, node.name);
      assert.ok(check.inside, node.name);
      for (const child of node.children)
        assert.ok(
          fit.points.some((p) => p.key === child.key),
          `${node.name}: label of ${child.name}`,
        );
    }
  });

test("rotated headings keep every microdistrict polygon inside", () => {
  for (const yaw of [0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2, 2.4]) {
    const { fit, camera, viewport } = fitted(largest, 1440, 900, yaw),
      ground = largest.children.flatMap((c) =>
        c.polygon.map((p) => ({ ...p, y: 0, left: 0, right: 0, top: 0, bottom: 0 })),
      );
    assert.ok(fit.complete, String(yaw));
    assert.ok(projectedFit(ground, camera, 1440, 900, viewport).inside, String(yaw));
  }
});

test("the camera comes as close as the screen allows, not farther", () => {
  for (const node of districts.filter((n) => n.children.length > 2)) {
    const { fit, camera, viewport } = fitted(node, 1440, 900);
    if (fit.distance < MIN_DISTANCE * 1.5 * 1.1) continue;
    const closer = camera.position
      .clone()
      .lerp(fit.target, 0.08);
    camera.position.copy(closer);
    camera.lookAt(fit.target);
    assert.equal(
      projectedFit(fit.points, camera, 1440, 900, viewport).inside,
      false,
      node.name,
    );
  }
});

test("automatic views share one elevation and keep the heading", () => {
  const { fit } = fitted(largest, 1440, 900, 1.1),
    offset = fit.position.clone().sub(fit.target).normalize();
  assert.ok(Math.abs(offset.y - FRAME_ELEVATION) < 1e-6);
  assert.ok(Math.abs(Math.atan2(offset.x, offset.z) - 1.1) < 1e-6);
});

test("whole city fits on desktop and phone within the far plane", () => {
  for (const [w, h] of [
    [1440, 900],
    [390, 844],
  ]) {
    const { fit, check } = fitted(model.root, w, h);
    assert.ok(fit.complete && check.inside, `${w}x${h}`);
    assert.ok(Number.isFinite(fit.distance) && fit.distance < 1500);
  }
});

test("product framing is finite and respects the minimum distance", () => {
  const product = model.products[0],
    { fit, check } = fitted(product, 1440, 900);
  assert.ok(check.inside);
  for (const v of [...fit.position.toArray(), ...fit.target.toArray()])
    assert.ok(Number.isFinite(v));
  assert.ok(fit.distance >= MIN_DISTANCE * 1.5 - 1e-6);
});

test("leaf framing includes the real buildings and the leaf label", () => {
  const leaf = model.products[0].parent,
    points = selectionPoints(leaf, { productEntity: entity }),
    keys = new Set(points.map((p) => p.key));
  assert.ok(keys.has(leaf.key));
  for (const p of model.products.filter((p) => p.parent === leaf))
    assert.ok(keys.has(p.key), p.key);
  assert.ok(fitted(leaf, 390, 844).fit.complete);
});

test("roof clearance and minimum distance push the camera back, never down", () => {
  const camera = new THREE.PerspectiveCamera(42, 1.6, 0.05, 1600),
    leaf = model.products[0].parent,
    viewport = view(1440, 900),
    fit = fitCamera({
      node: leaf,
      camera,
      width: 1440,
      height: 900,
      viewport,
      bounds,
      productEntity: entity,
      floorAt: () => 120,
    });
  assert.ok(fit.position.y >= 120 - 1e-6);
  assert.ok(fit.complete);
});

test("a selected district keeps every microdistrict open at any zoom", () => {
  for (const node of districts.filter((n) => n.children.length))
    for (const ppu of [0.1, 1, 10, 100]) {
      const states = semanticAreas(model, ppu, node);
      for (const child of node.children)
        assert.ok(
          states.some((s) => s.node === child && s.opacity === 1),
          `${node.name}/${child.name} at ${ppu}`,
        );
      assert.ok(!states.some((s) => s.node.parent?.parent === node));
    }
});

test("selected leaf and selected product's leaf stay visible when zoomed out", () => {
  const leaf = model.products[0].parent;
  assert.ok(
    semanticAreas(model, 0.01, leaf).some((s) => s.node === leaf && s.opacity === 1),
  );
  assert.ok(
    semanticAreas(model, 0.01, model.products[0]).some(
      (s) => s.node === leaf && s.opacity === 1,
    ),
  );
  assert.deepEqual(
    semanticAreas(model, 0.5).map((s) => s.node.key),
    semanticAreas(model, 0.5, model.root).map((s) => s.node.key),
  );
});

test("continuous zoom converges monotonically", () => {
  let d = 120,
    previous = d;
  for (let i = 0; i < 60; i++) {
    d = dampDistance(d, 40, 1 / 30);
    assert.ok(d <= previous && d >= 40);
    previous = d;
  }
  assert.ok(Math.abs(d - 40) < 0.01);
});

test("continuous zoom does not depend on the frame rate", () => {
  let a = 200,
    b = 200;
  for (let i = 0; i < 30; i++) a = dampDistance(a, 30, 1 / 30);
  for (let i = 0; i < 60; i++) b = dampDistance(b, 30, 1 / 60);
  assert.ok(Math.abs(a - b) < 1e-8);
});

test("flight moves camera and target with the same easing", () => {
  const from = { x: 0, y: 40, z: 0 },
    to = { x: 100, y: 60, z: -50 };
  for (const t of [0.1, 0.35, 0.5, 0.8]) {
    const pose = flightPose(from, to, t, 0),
      e = flightEase(t);
    assert.ok(Math.abs(pose.x - 100 * e) < 1e-9);
    assert.ok(Math.abs(pose.z + 50 * e) < 1e-9);
  }
});
