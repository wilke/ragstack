/** @type {import('tailwindcss').Config} */
// Theme extension merged from the redesign handoff (tailwind.theme.js) — every
// token below is used by the RAGStack UI designs. `fontFamily.sans` deliberately
// replaces Tailwind's default stack so IBM Plex Sans is the app-wide body font.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          900: "#001f3f", // primary navy: header text, wordmark, headings, dark bands
          800: "#0b2338", // evidence panel background (light screens)
          700: "#071b2f", // dark screen background (Evidence tab)
          600: "#04121f", // drawer / deepest surface
          500: "#0f2c45", // source-viewer card on dark
        },
        accent: {
          DEFAULT: "#ffd100", // yellow: active tab underline, primary CTA, highlights
          soft: "#fffdf2", // yellow tint surface
          line: "#fff6d0", // inline claim highlight (light)
          text: "#7a6a17", // readable text on yellow tints
        },
        link: "#0b5aa8", // links, secondary actions, citation chips
        linkSoft: "#eaf2fa", // citation chip background
        sky: "#4b9cd3", // Elasticsearch / secondary source rule
        // Semantic (state) colors and the dim-text ramp resolve through CSS
        // variables (src/index.css) so the accessible-vision mode — toggled as
        // data-vision="accessible" on <html> (lib/vision.ts) — can swap them to
        // a color-vision-safe palette and raise the small-text contrast floor
        // without touching any component. Brand colors above stay literal: the
        // navy/yellow/blue axis is already distinguishable under the common
        // color-vision deficiencies.
        moss: "rgb(var(--c-moss) / <alpha-value>)", // Neo4j / grounded / healthy
        mossSoft: "rgb(var(--c-moss-soft) / <alpha-value>)",
        rust: "rgb(var(--c-rust) / <alpha-value>)", // off-topic / failed
        rustSoft: "rgb(var(--c-rust-soft) / <alpha-value>)",
        amber: "rgb(var(--c-amber) / <alpha-value>)", // degraded / cold start
        paper: "#f7f6f3", // page-level warm surface
        line: "#e3e2de", // hairline border
        lineSoft: "#f0efec", // table row divider
        muted: "rgb(var(--c-muted) / <alpha-value>)", // eyebrow / label text
        dim: "rgb(var(--c-dim) / <alpha-value>)", // metadata text
        faint: "rgb(var(--c-faint) / <alpha-value>)", // disabled / placeholder
        body: "#3d3d38", // body copy
        strong: "#1b2733", // emphasised body copy
      },
      fontFamily: {
        display: ["Archivo", "system-ui", "sans-serif"], // headings, titles, stats
        sans: ["IBM Plex Sans", "system-ui", "sans-serif"], // body, UI
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"], // metadata, ids, scores
      },
      borderRadius: { pill: "24px", chip: "16px", card: "8px", panel: "6px", row: "5px" },
      boxShadow: {
        popover: "0 12px 34px rgba(0,20,50,.16)",
        drawer: "-24px 0 60px rgba(4,18,31,.35)",
      },
    },
  },
  plugins: [],
};
