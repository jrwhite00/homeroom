"use client";

import { use, useEffect, useRef, useState } from "react";
import KubeView from "../../KubeView";
import { lastTalk, roomsByHour, track } from "../../lib/classroom.mjs";

// The whole class at once: who is in which talk, live. When a talk is cancelled, Kubernetes moves its
// students out and the scheduler finds each one another talk in the same hour that they're ready for;
// this page shows them arrive, with where they came from. Read from GET /api/placements?class=,
// which the placer's controller loop updates every 5 seconds.

const REFRESH_MS = 3000;
const MOVED_FOR_MS = 60000; // how long a "moved here" note stays up
const DAYS = { mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday", fri: "Friday", sat: "Saturday", sun: "Sunday" };
const NAVY = "#14123a";
const ACCENT = "#ff5d7d";

// "thu-1100" -> "Thursday 11:00 AM". Slots are already America/Denver times.
function hourLabel(slot) {
  const m = /^([a-z]{3})-(\d{2})(\d{2})$/.exec(slot || "");
  if (!m || !DAYS[m[1]]) return slot;
  const h = Number(m[2]);
  return `${DAYS[m[1]]} ${((h + 11) % 12) + 1}:${m[3]} ${h < 12 ? "AM" : "PM"}`;
}

function Student({ p, moved, was }) {
  return (
    <li style={{ display: "inline-block", margin: "0 0.4rem 0.4rem 0", padding: "0.25rem 0.6rem", borderRadius: 999,
                 border: `2px solid ${moved ? ACCENT : "#d9dbea"}`, background: moved ? "#fff0f3" : "#fff" }}>
      <strong>{p.student}</strong>
      {moved && <span> · moved here from “{moved.fromTitle}”</span>}
      {!moved && was && <span> · was in “{was.title}”</span>}
    </li>
  );
}

export default function ClassPage({ params }) {
  const { id } = use(params);
  const [placements, setPlacements] = useState(null);
  const [error, setError] = useState(null);
  const [moved, setMoved] = useState({}); // key -> move, while it's recent
  const seen = useRef({});

  useEffect(() => {
    let stop = false;
    const load = () =>
      fetch(`/api/placements?class=${encodeURIComponent(id)}`)
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then((body) => {
          if (stop) return;
          const result = track(seen.current, body.placements);
          seen.current = result.seen;
          const now = Date.now();
          setMoved((m) => {
            const kept = Object.fromEntries(Object.entries(m).filter(([, v]) => now - v.at < MOVED_FOR_MS));
            result.moves.forEach((mv) => { kept[mv.key] = { ...mv, at: now }; });
            return kept;
          });
          setPlacements(body.placements);
          setError(null);
        })
        .catch(() => !stop && setError("We couldn't load this class right now. Trying again…"));
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, [id]);

  const hours = placements && roomsByHour(placements);
  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 900, margin: "0 auto", padding: "0 1rem 3rem",
                   lineHeight: 1.5, color: "#1a1a2e" }}>
      <header style={{ background: NAVY, color: "#fff", margin: "0 -1rem 1.5rem", padding: "1.5rem 1rem" }}>
        <h1 style={{ margin: 0 }}>Class {id}: who is in which talk</h1>
        <p style={{ margin: "0.4rem 0 0" }}>
          Live. When a talk is cancelled, Kubernetes moves its students out and finds each one another talk in the same
          hour that they&apos;re ready for. Anyone with nowhere to go waits, and the page says why.
        </p>
      </header>
      <p style={{ fontSize: "0.9rem", color: "#4b4b63" }}>
        Simulated: these students are fictional, with made-up names, and seat counts are simulated.
      </p>
      {error && <p role="alert">{error}</p>}
      {!hours && !error && <p>Loading…</p>}
      {hours && !hours.length && <p>No one in this class has a week yet.</p>}
      <div aria-live="polite">
        {hours?.map((h) => (
          <section key={h.slot} style={{ marginBottom: "1.5rem" }}>
            <h2 style={{ display: "inline-block", background: NAVY, color: "#fff", padding: "0.2rem 0.8rem",
                         borderRadius: 999, fontSize: "1.05rem" }}>{hourLabel(h.slot)}</h2>
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {h.talks.map((t) => (
                <li key={t.talk ?? "waiting"} style={{ border: "1px solid #d9dbea", borderRadius: 10, padding: "0.8rem 1rem",
                                                        marginBottom: "0.7rem", background: t.talk ? "#fff" : "#f6f6fa" }}>
                  <h3 style={{ margin: "0 0 0.5rem", fontSize: "1rem" }}>
                    {t.talk ? <>{t.title} <code style={{ fontWeight: 400, color: "#4b4b63" }}>{t.talk}</code></>
                      : "Waiting for a talk"}
                  </h3>
                  <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                    {t.students.map((p) => (
                      <Student key={p.student} p={p} moved={moved[`${p.student}:${p.slot}`]}
                               was={lastTalk(seen.current, p)} />
                    ))}
                  </ul>
                  {!t.talk && t.students.map((p) => p.plain && (
                    <p key={p.student} style={{ margin: "0.4rem 0 0" }}><strong>{p.student}:</strong> {p.plain}</p>
                  ))}
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
      <KubeView classId={id} startOpen />
    </main>
  );
}
