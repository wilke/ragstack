import { useRef, type ReactNode } from "react";
import { useDismissable } from "../useDismiss";

// Anchored disclosure used by Compare's "Edit defaults" link and each lane's
// "edit ▾" chip. Same close mechanics as QueryOptionsMenu (outside click +
// Escape); the trigger's look belongs to the caller (link vs chip), the panel
// is the shared white popover card.
//
// No title="": the visible label plus aria-expanded already say what the button
// does, and a hover-only restatement of it reached neither keyboard nor touch.
export function LeverPopover({
  label,
  buttonClassName,
  align = "left",
  children,
}: {
  label: ReactNode;
  buttonClassName: string;
  align?: "left" | "right";
  children: ReactNode;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useDismissable(wrap);

  return (
    <div className="relative inline-flex" ref={wrap}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="true"
        className={buttonClassName}
      >
        {label}
      </button>
      {open ? (
        <div
          className={`absolute top-full z-10 mt-1.5 w-72 rounded-panel border border-line bg-white p-3 shadow-popover ${
            align === "right" ? "right-0" : "left-0"
          }`}
        >
          {children}
        </div>
      ) : null}
    </div>
  );
}
