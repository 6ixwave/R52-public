// R-52: графики цен на SVG без сторонних библиотек.
import { svg, h, uid, clear, clamp, money, compactMoney, dateShort, dateTime } from "./dom.js";
import { motionOn, EASE_OUT } from "./motion.js";

const r2 = (value) => Math.round(value * 100) / 100;

// Монотонная кубическая интерполяция (Fritsch-Carlson): кривая не выходит за соседние точки
// и не рисует цен ниже или выше тех, что были на самом деле.
export function smoothPath(points) {
  const n = points.length;
  if (!n) return "";
  if (n === 1) return `M${r2(points[0][0])},${r2(points[0][1])}`;
  const dx = [];
  const slope = [];
  for (let i = 0; i < n - 1; i += 1) {
    dx[i] = points[i + 1][0] - points[i][0];
    slope[i] = dx[i] ? (points[i + 1][1] - points[i][1]) / dx[i] : 0;
  }
  const tangent = [slope[0]];
  for (let i = 1; i < n - 1; i += 1) tangent[i] = slope[i - 1] * slope[i] <= 0 ? 0 : (slope[i - 1] + slope[i]) / 2;
  tangent[n - 1] = slope[n - 2];
  for (let i = 0; i < n - 1; i += 1) {
    if (slope[i] === 0) {
      tangent[i] = 0;
      tangent[i + 1] = 0;
      continue;
    }
    const a = tangent[i] / slope[i];
    const b = tangent[i + 1] / slope[i];
    const s = a * a + b * b;
    if (s > 9) {
      const k = 3 / Math.sqrt(s);
      tangent[i] = k * a * slope[i];
      tangent[i + 1] = k * b * slope[i];
    }
  }
  let d = `M${r2(points[0][0])},${r2(points[0][1])}`;
  for (let i = 0; i < n - 1; i += 1) {
    const third = dx[i] / 3;
    d += `C${r2(points[i][0] + third)},${r2(points[i][1] + tangent[i] * third)},`
      + `${r2(points[i + 1][0] - third)},${r2(points[i + 1][1] - tangent[i + 1] * third)},`
      + `${r2(points[i + 1][0])},${r2(points[i + 1][1])}`;
  }
  return d;
}

const TREND = { down: ["#34d399", "#22d3ee"], up: ["#f472b6", "#fb7185"], flat: ["#a78bfa", "#22d3ee"] };

// Маленький график в карточке. Цвет показывает направление: зелёный дешевеет, розовый дорожает.
export function sparkline(points, { width = 120, height = 36, animate = true } = {}) {
  const data = (points || [])
    .map((point) => [Date.parse(point[0]), Number(point[1])])
    .filter((point) => point[1] !== null && Number.isFinite(point[1]));
  const root = svg("svg", { class: "spark", viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: "none", "aria-hidden": "true" });
  if (data.length < 2) {
    root.append(svg("line", { class: "spark-flat", x1: 2, x2: width - 2, y1: height / 2, y2: height / 2 }));
    return root;
  }
  const byTime = data.every((point) => Number.isFinite(point[0])) && data[data.length - 1][0] > data[0][0];
  const xs = data.map((point, index) => (byTime ? point[0] : index));
  const ys = data.map((point) => point[1]);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  let lo = Math.min(...ys);
  let hi = Math.max(...ys);
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = 3;
  const pts = xs.map((x, i) => [pad + ((x - x0) / (x1 - x0 || 1)) * (width - pad * 2), pad + (1 - (ys[i] - lo) / (hi - lo)) * (height - pad * 2)]);
  const trend = ys[ys.length - 1] < ys[0] ? "down" : ys[ys.length - 1] > ys[0] ? "up" : "flat";
  const [c1, c2] = TREND[trend];
  const id = uid("sp");
  const line = smoothPath(pts);
  const area = `${line}L${r2(pts[pts.length - 1][0])},${height}L${r2(pts[0][0])},${height}Z`;
  root.append(
    svg("defs", null,
      svg("linearGradient", { id: `${id}s`, gradientUnits: "userSpaceOnUse", x1: 0, y1: 0, x2: width, y2: 0 },
        svg("stop", { offset: "0", "stop-color": c1 }), svg("stop", { offset: "1", "stop-color": c2 })),
      svg("linearGradient", { id: `${id}a`, gradientUnits: "userSpaceOnUse", x1: 0, y1: 0, x2: 0, y2: height },
        svg("stop", { offset: "0", "stop-color": c2, "stop-opacity": ".28" }), svg("stop", { offset: "1", "stop-color": c2, "stop-opacity": "0" }))),
    svg("path", { d: area, fill: `url(#${id}a)` }),
    svg("path", { class: "spark-line", d: line, stroke: `url(#${id}s)` }));
  if (animate && motionOn()) {
    root.animate([{ clipPath: "inset(-30% 100% -30% 0)" }, { clipPath: "inset(-30% -10% -30% 0)" }], { duration: 1100, easing: EASE_OUT });
  }
  return root;
}

function niceStep(raw) {
  const power = 10 ** Math.floor(Math.log10(raw));
  const fraction = raw / power;
  return (fraction < 1.5 ? 1 : fraction < 3 ? 2 : fraction < 7 ? 5 : 10) * power;
}

// Большой график: сетка, подписи, цель слежения, медиана и перекрестие с подсказкой. series: [{ t: мс, v: копейки }].
export function priceChart(box, { series = [], target = null, median = null, currency = "RUB", animate = true } = {}) {
  clear(box);
  box.classList.remove("hover");
  const data = series.filter((p) => Number.isFinite(p.t) && Number.isFinite(p.v)).sort((a, b) => a.t - b.t);
  if (!data.length) {
    box.append(h("div", { class: "chart-empty", text: "Нет наблюдений с ценой и датой за этот период" }));
    return;
  }
  const width = Math.max(300, Math.round(box.clientWidth || 600));
  const height = Math.max(160, Math.round(box.clientHeight || 230));
  const pad = { left: 8, right: 70, top: 18, bottom: 26 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const values = data.map((p) => p.v);
  if (Number.isFinite(target)) values.push(target);
  if (Number.isFinite(median)) values.push(median);
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (hi === lo) {
    const delta = Math.max(100, Math.abs(hi) * 0.04);
    lo -= delta;
    hi += delta;
  }
  const span = hi - lo;
  lo = Math.max(0, lo - span * 0.14);
  hi += span * 0.14;
  let t0 = data[0].t;
  let t1 = data[data.length - 1].t;
  if (t1 === t0) { t0 -= 43200000; t1 += 43200000; }
  const X = (t) => pad.left + ((t - t0) / (t1 - t0)) * innerW;
  const Y = (v) => pad.top + (1 - (v - lo) / (hi - lo)) * innerH;
  const base = pad.top + innerH;
  const id = uid("ch");
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "График цены" });
  root.append(svg("defs", null,
    svg("linearGradient", { id: `${id}s`, gradientUnits: "userSpaceOnUse", x1: pad.left, y1: 0, x2: width - pad.right, y2: 0 },
      svg("stop", { offset: "0", "stop-color": "#a78bfa" }), svg("stop", { offset: ".5", "stop-color": "#818cf8" }), svg("stop", { offset: "1", "stop-color": "#22d3ee" })),
    svg("linearGradient", { id: `${id}a`, gradientUnits: "userSpaceOnUse", x1: 0, y1: pad.top, x2: 0, y2: base },
      svg("stop", { offset: "0", "stop-color": "#8b5cf6", "stop-opacity": ".34" }), svg("stop", { offset: "1", "stop-color": "#22d3ee", "stop-opacity": "0" }))));
  const step = niceStep((hi - lo) / 4);
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
    const y = r2(Y(v));
    root.append(svg("line", { class: "ch-grid", x1: pad.left, x2: width - pad.right + 6, y1: y, y2: y }),
      svg("text", { class: "ch-axis", x: width - pad.right + 12, y: r2(y + 3.5) }, compactMoney(v, currency)));
  }
  const labels = clamp(Math.floor(innerW / 130), 2, 4);
  for (let i = 0; i < labels; i += 1) {
    const t = t0 + ((t1 - t0) * i) / (labels - 1);
    const anchor = i === 0 ? "start" : i === labels - 1 ? "end" : "middle";
    root.append(svg("text", { class: "ch-axis", x: r2(X(t)), y: height - 6, "text-anchor": anchor }, dateShort(t)));
  }
  return drawSeries({ box, root, data, X, Y, base, width, height, pad, id, target, median, currency, animate });
}

function drawSeries({ box, root, data, X, Y, base, width, height, pad, id, target, median, currency, animate }) {
  const pts = data.map((p) => [X(p.t), Y(p.v)]);
  const linePath = smoothPath(pts);
  const last = pts[pts.length - 1];
  const area = svg("path", { d: `${linePath}L${r2(last[0])},${r2(base)}L${r2(pts[0][0])},${r2(base)}Z`, fill: `url(#${id}a)` });
  const line = svg("path", { class: "ch-line", d: linePath, stroke: `url(#${id}s)`, pathLength: "1" });
  root.append(area, line);
  const tags = [];
  if (Number.isFinite(median)) tags.push({ cls: "median", value: median, text: `медиана ${money(median, currency)}` });
  if (Number.isFinite(target)) tags.push({ cls: "target", value: target, text: `цель ${money(target, currency)}` });
  let previous = null;
  for (const tag of tags) {
    const y = r2(Y(tag.value));
    root.append(svg("line", { class: `ch-${tag.cls}`, x1: pad.left, x2: width - pad.right, y1: y, y2: y }));
    let ty = y - 6;
    if (previous !== null && Math.abs(ty - previous) < 13) ty = y + 15;
    previous = ty;
    root.append(svg("text", { class: `ch-tag ${tag.cls}`, x: pad.left + 6, y: r2(ty) }, tag.text));
  }
  if (pts.length <= 60) {
    for (const [px, py] of pts) root.append(svg("circle", { cx: r2(px), cy: r2(py), r: 2.4, fill: "#c4b5fd", opacity: ".7" }));
  }
  root.append(
    svg("circle", { class: "ch-halo", cx: r2(last[0]), cy: r2(last[1]), r: 6, fill: "#22d3ee" }),
    svg("circle", { cx: r2(last[0]), cy: r2(last[1]), r: 4, fill: "#ffffff", stroke: "#22d3ee", "stroke-width": 2.5 }));
  const cross = svg("line", { class: "ch-cross", x1: 0, x2: 0, y1: pad.top, y2: r2(base) });
  const dot = svg("circle", { class: "ch-dot", cx: 0, cy: 0, r: 5 });
  const overlay = svg("rect", { x: pad.left, y: 0, width: Math.max(1, width - pad.left - pad.right), height, fill: "transparent" });
  root.append(cross, dot, overlay);
  const tipValue = h("b");
  const tipDate = h("small", { class: "faint" });
  const tip = h("div", { class: "chart-tip" }, tipValue, tipDate);
  box.append(root, tip);
  const move = (event) => {
    const rect = root.getBoundingClientRect();
    if (!rect.width) return;
    const px = ((event.clientX - rect.left) / rect.width) * width;
    let best = 0;
    for (let i = 1; i < pts.length; i += 1) {
      if (Math.abs(pts[i][0] - px) < Math.abs(pts[best][0] - px)) best = i;
    }
    const [bx, by] = pts[best];
    cross.setAttribute("x1", r2(bx));
    cross.setAttribute("x2", r2(bx));
    dot.setAttribute("cx", r2(bx));
    dot.setAttribute("cy", r2(by));
    tipValue.textContent = money(data[best].v, currency);
    tipDate.textContent = dateTime(data[best].t);
    const left = clamp((bx / width) * rect.width, 70, Math.max(70, rect.width - 70));
    tip.style.setProperty("left", `${r2(left)}px`);
    tip.style.setProperty("top", `${r2((by / height) * rect.height)}px`);
    box.classList.add("hover");
  };
  overlay.addEventListener("pointermove", move);
  overlay.addEventListener("pointerdown", move);
  overlay.addEventListener("pointerleave", () => box.classList.remove("hover"));
  if (animate && motionOn()) {
    line.animate([{ strokeDasharray: "1 1", strokeDashoffset: 1 }, { strokeDasharray: "1 1", strokeDashoffset: 0 }], { duration: 1300, easing: EASE_OUT });
    area.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 900, delay: 250, easing: EASE_OUT, fill: "backwards" });
  }
  return { points: pts.length };
}
