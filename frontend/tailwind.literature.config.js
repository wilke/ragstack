// Tailwind for the LITERATURE bundle only (vite.literature.config.ts).
//
// The theme is IMPORTED from the shared config rather than copied: the two must
// render the same design tokens, and a duplicated 60-line theme block is
// byte-identical right up until someone changes one of them. Only `content`
// differs — the literature app's utilities are emitted into its own stylesheet
// instead of every tenant's.
import base from "./tailwind.config.js";

export default {
  ...base,
  content: ["./literature.html", "./src/literature/**/*.{ts,tsx}"],
};
