// R-52: анимации. Пружины считаются один раз и подставляются в CSS как linear().
import { number } from "./dom.js";

export const EASE_OUT = "cubic-bezier(.16, 1, .3, 1)";
export const EASE_IN = "cubic-bezier(.4, 0, 1, 1)";

const reducedMotion=typeof window!=='undefined'&&window.matchMedia?window.matchMedia('(prefers-reduced-motion: reduce)'):null;
export function motionOn() {
  return document.documentElement.dataset.motion !== "off" && !reducedMotion?.matches;
}
reducedMotion?.addEventListener('change',()=>applyMotion(storedMotion()));

export function applyMotion(enabled) {
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.documentElement.dataset.motion = enabled && !reduce ? "on" : "off";
  try { localStorage.setItem("r52.motion", enabled ? "1" : "0"); } catch { /* хранилище недоступно */ }
}

export function storedMotion() {
  try { return localStorage.getItem("r52.motion") !== "0"; } catch { return true; }
}

// Затухающая пружина с шагом 1/240 с. Точки идут равномерно по времени.
export function springEasing(stiffness = 220, damping = 21, mass = 1, points = 64) {
  const dt = 1 / 240;
  let x = 0;
  let v = 0;
  let t = 0;
  const samples = [];
  while (t < 3) {
    const a = (-stiffness * (x - 1) - damping * v) / mass;
    v += a * dt;
    x += v * dt;
    t += dt;
    samples.push(x);
    if (Math.abs(1 - x) < 0.0005 && Math.abs(v) < 0.005) break;
  }
  const stops = ["0"];
  const step = Math.max(1, Math.floor(samples.length / points));
  for (let i = step; i < samples.length - 1; i += step) stops.push(trim(samples[i]));
  stops.push("1");
  return { easing: `linear(${stops.join(", ")})`, duration: Math.round(t * 1000) };
}

function trim(value) {
  return value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}

export function installSprings() {
  if (!(window.CSS && CSS.supports && CSS.supports("transition-timing-function", "linear(0, 1)"))) return;
  const root = document.documentElement.style;
  root.setProperty("--spring", springEasing(220, 21).easing);
  root.setProperty("--spring-soft", springEasing(150, 19).easing);
}

// Появление блоков лесенкой. fill backwards: до старта элемент скрыт, после снова подчиняется CSS.
export function enter(targets, { y = 14, x = 0, scale = 0.985, delay = 0, step = 40, duration = 640, blur = 0, maxSteps = 14 } = {}) {
  if (!motionOn() || !targets) return;
  const list = targets instanceof Element ? [targets] : Array.from(targets);
  list.forEach((el, index) => {
    if (!(el instanceof Element)) return;
    const from = { opacity: 0, transform: `translate(${x}px, ${y}px) scale(${scale})` };
    const to = { opacity: 1, transform: "none" };
    if (blur) {
      from.filter = `blur(${blur}px)`;
      to.filter = "blur(0px)";
    }
    el.animate([from, to], { duration, delay: delay + Math.min(index, maxSteps) * step, easing: EASE_OUT, fill: "backwards" });
  });
}

export function countUp(el, to, { duration = 1000, format = (value) => number(Math.round(value)) } = {}) {
  const target = Number(to) || 0;
  const from = Number(el.dataset.value || 0);
  el.dataset.value = String(target);
  if (!motionOn() || from === target) {
    el.textContent = format(target);
    return;
  }
  const start = performance.now();
  const tick = (now) => {
    const progress = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - progress, 4);
    el.textContent = format(from + (target - from) * eased);
    if (progress < 1 && el.dataset.value === String(target)) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

// Подсветка под курсором для всех .spot: один обработчик на документ, не чаще кадра.
export function spotlight(root) {
  let frame = 0;
  let last = null;
  root.addEventListener("pointermove", (event) => {
    last = event;
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      const target = last.target instanceof Element ? last.target.closest(".spot") : null;
      if (!target) return;
      const rect = target.getBoundingClientRect();
      target.style.setProperty("--mx", `${Math.round(last.clientX - rect.left)}px`);
      target.style.setProperty("--my", `${Math.round(last.clientY - rect.top)}px`);
    });
  }, { passive: true });
}

// Удаление из списка: сначала уезжает вбок, потом схлопывается по высоте.
export async function collapse(el, { duration = 560 } = {}) {
  if (!el || !el.isConnected) return;
  if (!motionOn()) {
    el.remove();
    return;
  }
  const height = el.getBoundingClientRect().height;
  el.style.setProperty("overflow", "hidden");
  const animation = el.animate([
    { opacity: 1, transform: "none", height: `${height}px` },
    { opacity: 0, transform: "translateX(28px) scale(.98)", height: `${height}px`, offset: 0.45 },
    { opacity: 0, transform: "translateX(28px) scale(.98)", height: "0px", paddingTop: "0px", paddingBottom: "0px", marginTop: "0px", marginBottom: "0px", borderTopWidth: "0px", borderBottomWidth: "0px" },
  ], { duration, easing: "cubic-bezier(.65, 0, .35, 1)", fill: "forwards" });
  try { await animation.finished; } catch { /* анимация отменена */ }
  el.remove();
}

export function pop(el) {
  if (!el || !motionOn()) return;
  el.classList.remove("pop");
  void el.offsetWidth;
  el.classList.add("pop");
  el.addEventListener("animationend", () => el.classList.remove("pop"), { once: true });
}

export function flash(el) {
  if (!el || !motionOn()) return;
  el.animate([
    { boxShadow: "0 0 0 0 rgba(167, 139, 250, 0)" },
    { boxShadow: "0 0 0 6px rgba(167, 139, 250, .32)", offset: 0.35 },
    { boxShadow: "0 0 0 0 rgba(167, 139, 250, 0)" },
  ], { duration: 1000, easing: EASE_OUT });
}
