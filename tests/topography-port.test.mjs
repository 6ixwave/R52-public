import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as topography from "../r52/web/js/vendor/topography.js";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

// Normalized settings of the React Bits "Sonar" preset (Topography defaults + preset).
const SONAR = {
  color: "#2fe0c8",
  lineCount: 26,
  lineWidth: 1.2,
  majorLines: true,
  elevation: 1,
  foreground: 0.2,
  scale: 1,
  detail: 0.35,
  tilt: 24,
  speed: 0.25,
  direction: [0, 1],
  morph: 0.2,
  glow: 1,
  depthOfField: 0.7,
  fog: 0.35,
  brightness: 1,
  opacity: 1,
  mouseInteraction: true,
  hoverEffect: "light",
  mouseRadius: 1,
  mouseStrength: 0.6,
  trail: 0.3,
  parallax: 0.5,
  clickEffect: "pulse",
  markers: 1,
  glitch: 0.35,
  intro: true,
  lightMode: false,
};

test("the port loads without a DOM and exposes the component API", () => {
  assert.equal(typeof topography.createTopography, "function");
  assert.equal(typeof topography.topographyPreset, "function");
  assert.equal(typeof topography.topographySettings, "function");
  assert.ok(JSON.stringify(topography.PRESET_OPTIONS).toLowerCase().includes("sonar"));
});

test("the Sonar preset matches React Bits", () => {
  const settings = topography.topographySettings(topography.topographyPreset("sonar"));
  assert.deepEqual(JSON.parse(JSON.stringify(settings)), SONAR);
  assert.equal(topography.topographyPreset("sonar", { light: true }).color, "#0f766e");
  const quiet = topography.topographySettings({
    ...topography.topographyPreset("sonar"),
    reducedMotion: true,
  });
  assert.equal(quiet.reducedMotion, true);
});

test("the overview widget runs Topography Sonar in the same box", () => {
  const overview = read("../r52/web/js/views/overview.js"),
    urban = read("../r52/web/css/urban.css");
  assert.match(overview, /import\("\.\.\/vendor\/topography\.js"\)/);
  assert.match(overview, /topographyPreset\("sonar"\)/);
  assert.match(overview, /reducedMotion: !motionOn\(\)/);
  assert.match(overview, /topo\?\.destroy\(\)/);
  assert.doesNotMatch(overview, /city-scene\.js|updateMini|buildTopology/);
  assert.match(overview, /class: "terrain-caption",[\s\S]*?go\("#\/map"\)/);
  // The widget keeps its 2.11 size at every breakpoint.
  assert.ok(urban.includes(".radar.terrain-preview{width:320px;height:236px;border-radius:32px"));
  assert.ok(urban.includes(".radar.terrain-preview{width:280px;height:224px}"));
  assert.ok(urban.includes(".radar.terrain-preview{width:100%;height:220px;border-radius:28px"));
  const added = urban.slice(urban.indexOf("R52 2.12: overview widget"));
  // Only the inner layers are styled; no new pixel size for the widget box.
  assert.doesNotMatch(added, /(?:^|[;{])\s*(?:width|height)\s*:\s*\d+px/m);
});

test("the port credits React Bits under its license", () => {
  const source = read("../r52/web/js/vendor/topography.js"),
    license = read("../r52/web/js/vendor/REACT-BITS-LICENSE.txt"),
    notices = read("../THIRD-PARTY.md");
  assert.match(source.slice(0, 1600), /React Bits/);
  assert.match(source.slice(0, 1600), /REACT-BITS-LICENSE\.txt/);
  assert.match(license, /Commons Clause/);
  assert.match(license, /topography\.js/);
  assert.match(notices, /vendor\/topography\.js/);
  assert.doesNotMatch(source, /from "react"|useEffect|useRef/);
});
