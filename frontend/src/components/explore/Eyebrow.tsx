// Section eyebrow — the design's tracked-uppercase mono label (Answer,
// Sources, This run…). An h2 so screen readers still get the outline the old
// headings provided.

import type { ReactNode } from "react";

export function Eyebrow({
  id,
  className = "",
  children,
}: {
  id?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <h2
      id={id}
      className={`font-mono text-[10.5px] font-medium uppercase tracking-[.15em] text-muted ${className}`}
    >
      {children}
    </h2>
  );
}
