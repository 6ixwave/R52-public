// One primary action per object, without hijacking its secondary controls.
const CONTROLS =
  'a,button,input,select,textarea,summary,details,video,audio,[contenteditable="true"],[role="button"],[role="link"],[data-card-ignore]';
export function canActivateObject(root, event) {
  if (
    event.defaultPrevented ||
    event.button > 0 ||
    event.ctrlKey ||
    event.metaKey ||
    event.altKey ||
    event.shiftKey
  )
    return false;
  const target = event.target;
  if (!target?.closest) return false;
  const control = target.closest(CONTROLS);
  if (control && control !== root) return false;
  if (typeof window !== "undefined" && window.getSelection()?.toString().trim())
    return false;
  return true;
}
export function activateObject(
  el,
  open,
  { label, role = "link", dialog = false } = {},
) {
  el.classList.add("interactive-object");
  el.tabIndex = 0;
  el.setAttribute("role", role);
  if (label) el.setAttribute("aria-label", label);
  if (dialog) el.setAttribute("aria-haspopup", "dialog");
  el.addEventListener("click", (event) => {
    if (canActivateObject(el, event)) open();
  });
  el.addEventListener("keydown", (event) => {
    if (
      event.target !== el ||
      event.repeat ||
      !["Enter", " "].includes(event.key)
    )
      return;
    event.preventDefault();
    open();
  });
  return el;
}
