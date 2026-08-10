// Accessible-vision mode: color-vision-safe state colors + a raised small-text
// contrast floor. The mode is a data attribute on <html> so the CSS-variable
// overrides in index.css apply app-wide, including portals/popovers; the
// preference persists in localStorage (unscoped by tenant path on purpose — it
// is about the viewer's eyes, not the deployment being addressed).

const KEY = "ragstack.visionMode";
const ATTR = "data-vision";
const ACCESSIBLE = "accessible";

export function getAccessibleVision(): boolean {
  try {
    return localStorage.getItem(KEY) === ACCESSIBLE;
  } catch {
    return false;
  }
}

export function setAccessibleVision(on: boolean): void {
  try {
    if (on) localStorage.setItem(KEY, ACCESSIBLE);
    else localStorage.removeItem(KEY);
  } catch {
    /* storage disabled → mode still applies for this page via the attribute */
  }
  applyVisionMode(on);
}

/** Stamp/unstamp the attribute; called at App mount and by the toggle. */
export function applyVisionMode(on: boolean = getAccessibleVision()): void {
  const root = document.documentElement;
  if (on) root.setAttribute(ATTR, ACCESSIBLE);
  else root.removeAttribute(ATTR);
}
