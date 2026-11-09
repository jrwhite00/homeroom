"use client";

import { useEffect, useId, useState } from "react";

// The jargon decoder. Renders text segments from the backend; a decoded term shows its concept's
// definition from the teachers' concept table, word for word. Nothing here writes a definition.
// The definition opens on keyboard focus as well as on hover, and Escape closes it.
function Term({ text, concepts }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);
  return (
    <span style={{ position: "relative", display: "inline" }}
          onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button type="button" aria-describedby={id} aria-expanded={open}
              onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
              style={{ font: "inherit", color: "inherit", background: "none", border: 0, padding: 0, cursor: "help",
                       borderBottom: "2px dotted #3b6fb6" }}>
        {text}
      </button>
      <span id={id} role="tooltip" hidden={!open}
            style={{ position: "absolute", left: 0, top: "1.6em", zIndex: 10, width: "min(22rem, 80vw)",
                     background: "#fff", color: "#1a1a1a", border: "1px solid #3b6fb6", borderRadius: 6,
                     padding: "0.5rem 0.7rem", boxShadow: "0 4px 14px rgba(0,0,0,.15)", fontSize: "0.95rem",
                     lineHeight: 1.4 }}>
        {concepts.map((c) => (
          <span key={c.slug} style={{ display: "block" }}>
            <strong>{c.concept}</strong>: <span data-definition>{c.definition}</span>
          </span>
        ))}
      </span>
    </span>
  );
}

export default function Decoded({ segments }) {
  return (
    <>
      {segments.map((s, i) => (s.concepts ? <Term key={i} text={s.text} concepts={s.concepts} /> : <span key={i}>{s.text}</span>))}
    </>
  );
}
