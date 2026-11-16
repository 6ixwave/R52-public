import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chooseLabels } from "../r52/web/js/city-label-layout.js";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("a district label that would cover a callout roof anchor is hidden", () => {
  const bounds = { x: 0, y: 0, width: 800, height: 600 };
  const covering = {
    rect: { x: 300, y: 200, width: 120, height: 46 },
    priority: 10,
    order: 0,
  };
  const clear = {
    rect: { x: 560, y: 380, width: 120, height: 46 },
    priority: 0,
    order: 1,
  };
  const anchor = { x: 341, y: 211, width: 18, height: 18 };
  assert.deepEqual(chooseLabels([covering, clear], bounds, [anchor]), [clear]);
  assert.deepEqual(chooseLabels([covering, clear], bounds), [covering, clear]);
});

test("the leaf label steps aside while its product callouts are shown", () => {
  const panels = read("../r52/web/js/city-panels.js"),
    labels = read("../r52/web/js/city-district-labels.js"),
    scene = read("../r52/web/js/city-scene.js");
  assert.match(
    panels,
    /avoid: \(\) => \(\{ scope: calloutScope, rects: avoidRects \}\)/,
  );
  assert.match(panels, /calloutScope = layout && shown\.length \? scope\.key : null;/);
  assert.match(labels, /node\.key === avoid\.scope\)\) continue;/);
  assert.match(labels, /obstacles\.push\(\.\.\.avoid\.rects\)/);
  assert.match(labels, /avoidKey\(avoid\)/);
  assert.match(scene, /avoid: panels \? panels\.avoid\(\) : null,/);
});
