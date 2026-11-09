"use client";

import { useEffect, useId, useRef, useState } from "react";
import Decoded from "./Decoded";

// The week as Kubernetes sees it. Each hour of a student's week is a pod; this shows, for each one,
// what `kubectl describe pod` shows: its name, namespace, phase, node and latest event, word for word,
// as the placer's controller loop last recorded them (GET /api/placements?class=).
// Under each hour is one plain sentence, worked out by the API from taints vs tolerations.
// Every Kubernetes word goes through the jargon decoder (POST /api/decode), so it is explained
// as soon as a teacher's concept lists it as a technical word. Nothing here writes a definition.

const REFRESH_MS = 5000; // the controller loop records a pass every 5 seconds
const DAYS = { mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday", fri: "Friday", sat: "Saturday", sun: "Sunday" };
const LABELS = ["Pod name", "Namespace", "Phase", "Node", "Latest event"];
const NO_NODE = "<none>"; // what kubectl describe shows before the scheduler picks a node

// "thu-1430" -> "Thursday 2:30 PM". Slots are already America/Denver times.
function hourLabel(slot) {
  const m = /^([a-z]{3})-(\d{2})(\d{2})$/.exec(slot || "");
  if (!m || !DAYS[m[1]]) return slot;
  const h = Number(m[2]);
  return `${DAYS[m[1]]} ${((h + 11) % 12) + 1}:${m[3]} ${h < 12 ? "AM" : "PM"}`;
}

function eventTime(at) {
  if (!at) return null;
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleTimeString("en-US", { timeZone: "America/Denver", hour: "numeric",
                                                                             minute: "2-digit", second: "2-digit" });
}

// One plain sentence for the hour. The missing concept comes from the API, never from the event text.
function plainSentence(p) {
  if (p.plain) return p.plain;
  if (p.phase === "Running" || p.phase === "Succeeded") {
    return p.title ? `You have a seat at “${p.title}”.` : "You have a seat at a talk in this hour.";
  }
  if (p.phase === "Missing") return "Kubernetes is setting this hour up again. Check back in a moment.";
  return "Homeroom hasn't checked this hour yet. Check back in a moment.";
}

// Every string on the page that may hold a Kubernetes word.
function wordsToDecode(placements) {
  const texts = new Set([...LABELS, NO_NODE, "pod"]);
  for (const p of placements) {
    [p.phase, p.talk, p.event?.type, p.event?.reason, p.event?.from, p.event?.message].forEach((t) => t && texts.add(t));
  }
  return [...texts];
}

// A word joined to a name by "-", "/" or "." is part of that name ("class" in "class-demo/maya-thu-1430"),
// not the concept, so it stays plain. The text itself is never changed.
function outsideNames(segments) {
  return segments.map((s, i) => {
    const before = segments[i - 1]?.text.slice(-2) || "";
    const after = segments[i + 1]?.text.slice(0, 2) || "";
    const joined = /\w[-/.]$/.test(before) || /^[-/.]\w/.test(after) || /[-/]$/.test(before) || /^[-/]/.test(after);
    return s.concepts && joined ? { text: s.text } : s;
  });
}

function Words({ text, decoded }) {
  return <Decoded segments={outsideNames(decoded[text] || [{ text }])} />;
}

function Hour({ p, decoded, showStudent }) {
  const w = (text) => <Words text={text} decoded={decoded} />;
  const at = eventTime(p.event?.at);
  return (
    <li style={{ border: "1px solid #c8d3e3", borderRadius: 8, padding: "0.8rem 1rem", marginBottom: "0.8rem" }}>
      <h3 style={{ margin: "0 0 0.5rem", fontSize: "1.05rem" }}>
        {hourLabel(p.slot)}{showStudent ? ` · ${p.student}` : ""}
      </h3>
      <dl style={{ display: "grid", gridTemplateColumns: "max-content 1fr", gap: "0.25rem 1rem", margin: 0 }}>
        <dt>{w("Pod name")}</dt><dd style={{ margin: 0 }}><code>{p.pod || "—"}</code></dd>
        <dt>{w("Namespace")}</dt><dd style={{ margin: 0 }}><code>{p.namespace || "—"}</code></dd>
        <dt>{w("Phase")}</dt><dd style={{ margin: 0 }}>{w(p.phase)}</dd>
        <dt>{w("Node")}</dt>
        <dd style={{ margin: 0 }}><code>{w(p.talk || NO_NODE)}</code>{p.title ? <> ({p.title})</> : null}</dd>
        <dt>{w("Latest event")}</dt>
        <dd style={{ margin: 0 }}>
          {p.event ? (
            <>
              <span>{w(p.event.type)} · {w(p.event.reason)}{p.event.from ? <> · {w(p.event.from)}</> : null}
                {at ? <> · <time dateTime={p.event.at}>{at}</time></> : null}</span>
              <blockquote style={{ margin: "0.3rem 0 0", padding: "0.4rem 0.6rem", background: "#f3f6fa",
                                   borderLeft: "3px solid #3b6fb6", fontFamily: "ui-monospace, monospace",
                                   fontSize: "0.9rem", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                {w(p.event.message)}
              </blockquote>
            </>
          ) : "None yet"}
        </dd>
      </dl>
      <p style={{ margin: "0.6rem 0 0", fontWeight: 600 }}>{plainSentence(p)}</p>
    </li>
  );
}

export default function KubeView({ classId, student }) {
  const [open, setOpen] = useState(false);
  const [placements, setPlacements] = useState(null);
  const [error, setError] = useState(null);
  const [decoded, setDecoded] = useState({});
  const asked = useRef(new Set()); // texts already sent to the decoder
  const regionId = useId();

  // Read placements while the view is open, and keep it in step with the controller loop.
  useEffect(() => {
    if (!open || !classId) return;
    let stop = false;
    const load = () =>
      fetch(`/api/placements?class=${encodeURIComponent(classId)}`)
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then((body) => {
          if (stop) return;
          setPlacements(body.placements.filter((p) => !student || p.student === student));
          setError(null);
        })
        .catch(() => !stop && setError("We couldn't load your week right now. Try again in a moment."));
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, [open, classId, student]);

  // Send any new words to the jargon decoder. If it can't be reached, the words show as plain text.
  useEffect(() => {
    if (!placements) return;
    const pending = wordsToDecode(placements).filter((t) => !asked.current.has(t) && t.length <= 1000);
    for (let i = 0; i < pending.length; i += 100) {
      const texts = pending.slice(i, i + 100);
      texts.forEach((t) => asked.current.add(t));
      fetch("/api/decode", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ texts }) })
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then((body) => setDecoded((d) => ({ ...d, ...Object.fromEntries(texts.map((t, j) => [t, body.segments[j]])) })))
        .catch(() => texts.forEach((t) => asked.current.delete(t))); // try again on the next refresh
    }
  }, [placements]);

  const hours = placements && [...placements].sort((a, b) => (a.slot + a.student).localeCompare(b.slot + b.student));
  return (
    <section style={{ marginTop: "2rem" }}>
      <button type="button" aria-expanded={open} aria-controls={regionId} onClick={() => setOpen((o) => !o)}
              style={{ font: "inherit", padding: "0.5rem 0.9rem", borderRadius: 6, border: "2px solid #3b6fb6",
                       background: open ? "#3b6fb6" : "#fff", color: open ? "#fff" : "#1a1a1a", cursor: "pointer" }}>
        {open ? "Hide how Kubernetes sees my week" : "Show how Kubernetes sees my week"}
      </button>
      <div id={regionId} hidden={!open} style={{ marginTop: "1rem" }}>
        <p>
          Each hour of your week is a <Words text="pod" decoded={decoded} />. Kubernetes picks a talk for it, or
          leaves it waiting. This is what Kubernetes says, word for word. Words with a dotted underline are explained by
          your teachers.
        </p>
        {error && <p role="alert">{error}</p>}
        {open && !hours && !error && <p>Loading…</p>}
        {hours && !hours.length && <p>No hours in your week yet.</p>}
        {hours && hours.length > 0 && (
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {hours.map((p) => <Hour key={`${p.student}-${p.slot}`} p={p} decoded={decoded} showStudent={!student} />)}
          </ul>
        )}
      </div>
    </section>
  );
}
