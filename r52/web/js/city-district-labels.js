import * as THREE from "./vendor/three.module.js";
import { h, svg, count as productCount } from "./dom.js";
import { semanticAreas, clamp } from "./city-spatial-layout.js";
import {
  labelAnchor,
  labelSize,
  anchoredLabelRect,
  chooseLabels,
} from "./city-label-layout.js";
// Every label has exactly one world anchor. Collisions hide labels, never displace them.
// While a leaf shows its product callouts, its own floating label repeats the
// breadcrumbs and the context bar and would cover the roof anchors.
const avoidKey = (avoid) =>
  avoid
    ? (avoid.scope || "") +
      ":" +
      (avoid.rects || [])
        .map((r) => Math.round(r.x) + "," + Math.round(r.y))
        .join(";")
    : "";
export function createDistrictLabels(host, onSelect) {
  const layer = h("nav", {
      class: "city-glass-labels",
      "aria-label": "Районы и микрорайоны",
    }),
    leaders = svg("svg", {
      class: "city-glass-leaders",
      "aria-hidden": "true",
    });
  host.append(leaders, layer);
  const entries = new Map(),
    v = new THREE.Vector3();
  let current = null,
    rects = [],
    hoverKey = null,
    activeKey = null,
    lastView = null,
    cachedStates = [];
  const activity = (key) => {
    hoverKey = key;
    for (const e of entries.values())
      e.el.classList.toggle("is-active", e.node.key === key);
  };
  function clear() {
    layer.replaceChildren();
    leaders.replaceChildren();
    entries.clear();
    rects = [];
    current = null;
    lastView = null;
    cachedStates = [];
    hoverKey = null;
    activeKey = null;
  }
  function make(node) {
    const name = h("span", { class: "city-label-name", text: node.name }),
      count = h("small", {
        class: "city-label-count",
        text: productCount(node.products.length, "товар", "товара", "товаров"),
      }),
      el = h(
        "button",
        {
          class: "city-glass-label",
          type: "button",
          title: node.path.join(" → "),
          "aria-label": `${node.name}, ${productCount(node.products.length, "товар", "товара", "товаров")}`,
          dataset: { key: node.key, depth: String(node.depth) },
          onClick: () => onSelect?.(node),
        },
        h("i", { "aria-hidden": "true" }),
        name,
        count,
      );
    el.style.setProperty("--district-color", node.color);
    el.addEventListener("pointerenter", () => activity(node.key));
    el.addEventListener("pointerleave", () => activity(null));
    el.addEventListener("focus", () => activity(node.key));
    el.addEventListener("blur", () => activity(null));
    el.tabIndex = -1;
    el.setAttribute("aria-hidden", "true");
    el.style.opacity = "0";
    layer.append(el);
    const { width } = labelSize(node);
    el.style.width = width + "px";
    const height = el.getBoundingClientRect().height;
    const line = svg("path", {
      fill: "none",
      stroke: node.color,
      "stroke-width": "1",
      "vector-effect": "non-scaling-stroke",
    });
    leaders.append(line);
    const entry = { node, el, line, width, height };
    entries.set(node.key, entry);
    return entry;
  }
  function update({
    model,
    camera,
    target,
    selection,
    hover,
    bounds = null,
    avoid = null,
  }) {
    if (!model) return [];
    if (current !== model) {
      clear();
      current = model;
    }
    const view =
      camera.matrixWorld.elements.join(",") +
      "|" +
      host.clientWidth +
      "|" +
      host.clientHeight +
      "|" +
      camera.aspect +
      "|" +
      camera.fov +
      "|" +
      selection?.key +
      "|" +
      hover?.key +
      "|" +
      hoverKey +
      "|" +
      (bounds
        ? [bounds.x, bounds.y, bounds.width, bounds.height]
            .map(Math.round)
            .join(",")
        : "") +
      "|" +
      avoidKey(avoid);
    if (view === lastView) return cachedStates;
    lastView = view;
    const w = host.clientWidth,
      height = host.clientHeight,
      distance = Math.max(1, camera.position.distanceTo(target)),
      ppu = height / (2 * Math.tan((camera.fov * Math.PI) / 360) * distance),
      states = semanticAreas(model, ppu, selection),
      focus = selection?.kind === "product" ? selection.parent : selection;
    activeKey = hoverKey || (hover?.kind === "area" ? hover.key : null);
    const obstacles = [
      ...host.querySelectorAll(
        ".city-map-header,.city-filterbar,.city-context",
      ),
    ]
      .filter((e) => !e.hidden)
      .map((e) => {
        const a = e.getBoundingClientRect(),
          b = host.getBoundingClientRect();
        return { x: a.x - b.x, y: a.y - b.y, width: a.width, height: a.height };
      });
    if (avoid?.rects?.length) obstacles.push(...avoid.rects);
    const candidates = [];
    for (const [order, state] of states.entries()) {
      const node = state.node;
      if(focus?.kind==='area') {
        if(node!==focus && node.parent!==focus && node!==focus.parent)continue;
      } else if(node.depth>0)continue;
      const alpha = clamp((state.opacity - 0.45) / 0.15, 0, 1);
      if (alpha < 0.04 || (avoid?.scope && node.key === avoid.scope)) continue;
      const anchor = labelAnchor(node);
      v.set(anchor.x, anchor.y, anchor.z).project(camera);
      if (v.z <= -1 || v.z >= 1 || Math.abs(v.x) > 1.1 || Math.abs(v.y) > 1.1)
        continue;
      const e = entries.get(node.key) || make(node),
        point = { x: (v.x * 0.5 + 0.5) * w, y: (-v.y * 0.5 + 0.5) * height },
        rect = anchoredLabelRect(point, e.width, e.height);
      candidates.push({
        e,
        node,
        rect,
        point,
        anchor,
        alpha,
        order,
        priority:
          (focus === node ? 100 : 0) +
          (focus && node.parent === focus ? 80 : 0) +
          (node.products.length ? 35 : 0) - Math.hypot(point.x-w/2,point.y-height/2)/150,
      });
    }
    const chosen = chooseLabels(
        candidates,
        bounds || {
          x: 8,
          y: w < 700 ? 212 : 144,
          width: w - 16,
          height: height - (w < 700 ? 212 : 144) - 100,
        },
        obstacles,
        w<700?5:10,
      ),
      visible = new Set(chosen.map((c) => c.node.key));
    rects = [];
    for (const e of entries.values()) {
      if (visible.has(e.node.key)) continue;
      e.el.style.opacity = "0";
      e.el.style.pointerEvents = "none";
      e.el.setAttribute("aria-hidden", "true");
      e.el.tabIndex = -1;
      e.line.style.opacity = "0";
      e.el.classList.remove("is-active");
    }
    for (const c of chosen) {
      const { e, node, rect, alpha } = c,
        active = activeKey === node.key,
        selected = selection === node;
      e.el.style.transform = `translate3d(${rect.x}px,${rect.y}px,0)`;
      e.el.style.opacity = String(alpha);
      e.el.style.pointerEvents = alpha > 0.2 ? "auto" : "none";
      e.el.setAttribute("aria-hidden", String(alpha <= 0.2));
      e.el.tabIndex = alpha > 0.2 ? 0 : -1;
      e.el.classList.toggle("is-active", active);
      e.el.classList.toggle("is-selected", selected);
      e.el.dataset.anchor = JSON.stringify(c.anchor);
      v.set(node.position.x, 0.1, node.position.z).project(camera);
      const ground = {
        x: (v.x * 0.5 + 0.5) * w,
        y: (-v.y * 0.5 + 0.5) * height,
      };
      e.line.setAttribute(
        "d",
        `M ${rect.x + rect.width / 2} ${rect.y + rect.height} L ${ground.x} ${ground.y}`,
      );
      e.line.style.opacity = active || selected ? String(alpha * 0.45) : "0";
      rects.push({
        ...rect,
        key: node.key,
        depth: node.depth,
        opacity: alpha,
        anchor: c.anchor,
        projected: c.point,
        type: "glass",
      });
    }
    cachedStates = states;
    return states;
  }
  return {
    update,
    clear,
    active: () => activeKey,
    rects: () => rects,
    stats: () => rects.map((r) => ({ ...r })),
    destroy() {
      clear();
      layer.remove();
      leaders.remove();
    },
  };
}
