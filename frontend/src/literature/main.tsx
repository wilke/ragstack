import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "../index.css";

const el = document.getElementById("literature-root");
if (!el) throw new Error("literature-root missing from literature.html");

createRoot(el).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
