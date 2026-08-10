import { useEffect, useState, type RefObject } from "react";

// One open/close state for every popover in the app (UserMenu, Explore's
// Options menu, Compare's lever popovers). Closes on an outside click or
// Escape — a menu that can only be closed by the button that opened it is a
// trap on touch. Listeners are attached only while open.
export function useDismissable(wrap: RefObject<HTMLElement>) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, wrap]);

  return [open, setOpen] as const;
}
