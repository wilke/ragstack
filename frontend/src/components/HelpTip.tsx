import { useEffect, useId, useRef, type ReactNode } from "react";
import { lookupTerm } from "../lib/glossary";
import { useDismissable } from "./useDismiss";

// The app's one help affordance, replacing native title="" tooltips — which are
// invisible to keyboard and touch users, unstyleable, and truncated by the OS.
//
// Two trigger shapes, one behaviour: <HelpTip term="top_k"/> renders the term as
// a dotted-underlined label, <HelpTip icon term="drift"/> renders a "?" button.
// `children` is the PANEL body; with only `term`, the definition comes from
// lib/glossary. Panel content is text-only by contract: anything focusable in
// there would need focus management this deliberately does not do (the tooltip
// never traps focus, and focus leaving the whole affordance closes it).
//
// Opens on hover AND focus, toggles on click (touch has no hover), closes on
// Escape, outside click and focus loss — the close mechanics come from
// useDismissable, same as every other popover here.
//
// A DISCLOSURE, not an ARIA tooltip: it opens on click and stays pinned, so the
// trigger carries aria-expanded and the panel carries no role="tooltip". The
// panel is always in the DOM (hidden while closed) so aria-describedby is stable
// from first render — several screen readers snapshot a control's description at
// focus time and would miss one injected by the focus event itself.
//
// The dark palette is deliberately literal (#dce8f3 on ink-500 = 11.5:1) rather
// than routed through the --c-* vision tokens: those have light-mode values only,
// and the dark values already clear 4.5:1 in both vision modes.

type Side = "top" | "bottom" | "left" | "right" | "bottom-end";

const SIDE: Record<Side, string> = {
  top: "bottom-full left-1/2 mb-2 -translate-x-1/2",
  bottom: "top-full left-1/2 mt-2 -translate-x-1/2",
  left: "right-full top-1/2 mr-2 -translate-y-1/2",
  right: "left-full top-1/2 ml-2 -translate-y-1/2",
  // End-aligned: a centred panel on a right-hand table column overflows its
  // `overflow-x-auto` wrapper, which clips both axes and cuts the copy off.
  "bottom-end": "top-full right-0 mt-2",
};

export function HelpTip({
  term,
  label,
  icon = false,
  side = "top",
  dark = false,
  className = "",
  children,
}: {
  term?: string; // glossary key; also the default trigger label and panel body
  label?: ReactNode; // trigger content when it should differ from `term`
  icon?: boolean; // render the "?" button instead of a text label
  side?: Side; // panel placement relative to the trigger
  dark?: boolean; // Evidence's ink grounds
  className?: string; // extra classes on the trigger
  children?: ReactNode; // panel body; overrides the glossary definition
}) {
  const id = useId();
  const wrap = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useDismissable(wrap);
  // A click PINS the panel open so leaving with the pointer doesn't close what
  // was deliberately opened; Escape/outside click/focus loss unpin it.
  const pinned = useRef(false);
  useEffect(() => {
    if (!open) pinned.current = false;
  }, [open]);

  const body = children ?? (term ? lookupTerm(term) : undefined);
  const trigger = label ?? term ?? null;

  // No definition and no children: show the label as plain text rather than a
  // button that opens an empty panel.
  if (body == null) return icon ? null : <span className={className}>{trigger}</span>;

  const triggerClass = icon
    ? // The 15px disc is under WCAG 2.2's 24x24 target; the ::before box gives it
      // a 24px hit area without taking any layout space.
      `relative inline-flex h-[15px] w-[15px] shrink-0 items-center justify-center rounded-full border text-[9.5px] font-semibold leading-none transition-colors before:absolute before:left-1/2 before:top-1/2 before:h-6 before:w-6 before:-translate-x-1/2 before:-translate-y-1/2 before:content-[''] motion-reduce:transition-none ${
        dark
          ? "border-white/40 text-[#dce8f3] hover:bg-white/10"
          : "border-muted text-body hover:bg-paper"
      } ${className}`
    : `cursor-help underline decoration-dotted underline-offset-2 ${
        dark ? "text-[#dce8f3]" : "text-body"
      } ${className}`;

  return (
    <span
      ref={wrap}
      className="relative inline-flex items-center"
      // Hover lives on the WRAPPER, not on the trigger: the panel is a sibling
      // offset by 8px, so moving the pointer onto it leaves the button — and an
      // unpinned tip would close before it could be read (WCAG 1.4.13).
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => {
        if (!pinned.current) setOpen(false);
      }}
      onFocus={() => setOpen(true)}
      onBlur={(e) => {
        // Focus moving within the affordance is not a dismissal — and a pointer
        // resting on a keyboard-focused trigger must not close it either.
        if (wrap.current?.contains(e.relatedTarget as Node | null)) return;
        pinned.current = false;
        setOpen(false);
      }}
      onKeyDown={(e) => {
        // Escape belongs to the innermost open layer. useDismissable listens on
        // `document`, so without this the enclosing Options/lever popover would
        // close too and the user would lose their place. React's root listener
        // runs before those document listeners, so stopping here is enough.
        if (e.key !== "Escape" || !open) return;
        e.stopPropagation();
        pinned.current = false;
        setOpen(false);
      }}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-describedby={id}
        aria-label={icon ? (term ? `About ${term}` : "More information") : undefined}
        onClick={() => {
          pinned.current = !pinned.current;
          setOpen(pinned.current);
        }}
        className={triggerClass}
      >
        {icon ? "?" : trigger}
      </button>
      {/* Always mounted, `hidden` while closed: the description has to exist when
          focus first lands on the trigger. No enter/exit transition either, so
          there is nothing for prefers-reduced-motion to suppress. */}
      <span
        id={id}
        hidden={!open}
        // Mousedown inside the panel would blur the trigger and close it, so the
        // copy could never be selected or copied.
        onMouseDown={(e) => e.preventDefault()}
        className={`absolute z-20 w-max max-w-[280px] whitespace-normal rounded-panel border p-2.5 text-left text-[12.5px] font-normal leading-snug shadow-popover ${
          dark ? "border-white/40 bg-ink-500 text-[#dce8f3]" : "border-line bg-white text-body"
        } ${SIDE[side]}`}
      >
        {body}
      </span>
    </span>
  );
}
