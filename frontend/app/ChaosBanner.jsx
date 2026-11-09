"use client";

import { useEffect, useState } from "react";

// Shows a clear label whenever the chaos switch (a simulated 800 ms slowdown on search) is on.
export default function ChaosBanner() {
  const [chaos, setChaos] = useState(null);

  useEffect(() => {
    let stop = false;
    const check = () =>
      fetch("/api/chaos")
        .then((r) => (r.ok ? r.json() : null))
        .then((c) => !stop && setChaos(c))
        .catch(() => {});
    check();
    const timer = setInterval(check, 10000);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, []);

  if (!chaos?.enabled) return null;
  return (
    <div role="status" style={{ background: "#fff4cc", borderBottom: "2px solid #c99a00", padding: "0.6rem 1rem",
                                 fontFamily: "system-ui, sans-serif" }}>
      <strong>Simulated slowdown is on.</strong> {chaos.label}
    </div>
  );
}
