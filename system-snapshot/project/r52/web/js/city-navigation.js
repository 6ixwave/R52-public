export const MIN_DISTANCE = 18;
export const MIN_ALTITUDE = 12;
export const MAX_POLAR = 1.08;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export function zoomStep(distance, input, min = MIN_DISTANCE, max = 900) {
  const d = Number.isFinite(distance) ? distance : min;
  return clamp(d + clamp(input, -3, 3) * Math.max(8, d * 0.22), min, max);
}
export function wheelInput(delta, mode = 0, height = 900) {
  return clamp(
    (delta * (mode === 1 ? 16 : mode === 2 ? height : 1)) / 120,
    -3,
    3,
  );
}
export function flightDuration(distance) {
  return clamp(400 + distance * 1.5, 450, 1100);
}
export function cityBounds(model) {
  const p =
    model.root.polygon ||
    model.root.children.flatMap((n) => [
      { x: n.position.x - n.radius, z: n.position.z - n.radius },
      { x: n.position.x + n.radius, z: n.position.z + n.radius },
    ]);
  return p.length
    ? {
        minX: Math.min(...p.map((v) => v.x)),
        maxX: Math.max(...p.map((v) => v.x)),
        minZ: Math.min(...p.map((v) => v.z)),
        maxZ: Math.max(...p.map((v) => v.z)),
      }
    : { minX: -50, maxX: 50, minZ: -50, maxZ: 50 };
}
export function boundedTarget(target, bounds, margin = 18) {
  return {
    x: clamp(target.x, bounds.minX - margin, bounds.maxX + margin),
    y: clamp(target.y, 0, 12),
    z: clamp(target.z, bounds.minZ - margin, bounds.maxZ + margin),
  };
}
// Camera position and orbit target share one easing, so the view never swings mid-flight.
export function flightEase(progress) {
  const t = clamp(progress, 0, 1);
  return t * t * (3 - 2 * t);
}
// Frame-rate independent exponential approach in log space: every wheel notch feels the same.
export function dampDistance(current, goal, dt, tau = 0.085) {
  const k = 1 - Math.exp(-clamp(Number.isFinite(dt) ? dt : 0, 0, 0.12) / tau);
  if (!(current > 0) || !(goal > 0)) return current + (goal - current) * k;
  return Math.exp(Math.log(current) + (Math.log(goal) - Math.log(current)) * k);
}
export function flightPose(from, to, progress, clearance = 32) {
  const t = clamp(progress, 0, 1),
    e = flightEase(t),
    lift =
      Math.sin(Math.PI * t) ** 2 *
      Math.max(0, clearance - Math.min(from.y, to.y));
  return {
    x: from.x + (to.x - from.x) * e,
    y: from.y + (to.y - from.y) * e + lift,
    z: from.z + (to.z - from.z) * e,
  };
}
