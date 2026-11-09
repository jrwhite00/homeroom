"use client";

import { useEffect, useId, useState } from "react";

// "My week as Kubernetes sees it": for each hour, the pod's name, phase, node and latest event,
// copied word for word from Kubernetes, then one plain sentence. The controller works out the
// missing concept by comparing the talk node's taints with the pod's tolerations.

const DAYS = { mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday", fri: "Friday", sat: "Saturday", sun: "Sunday" };

// Plain-language explanations of each Kubernetes word on this view.
const WORDS = [
  ["Pod", "One hour of your week. Kubernetes treats it like a small job that needs a place to run."],
  ["Phase", "Where your hour is right now. Pending means it's still waiting for a seat. Running means it has one."],
  ["Node", "A place a pod can go. Here, each talk is a node, so the node is the talk you're placed in."],
  ["Event", "A note Kubernetes writes about your pod, like a line in a diary. We show the newest one exactly as Kubernetes wrote it."],
  ["Normal", "An everyday step Kubernetes took for this hour, like giving you a seat. Nothing went wrong."],
  ["Warning", "Kubernetes ran into something it couldn't do yet, such as no talk having room. It keeps trying; this is a note, not a verdict on you."],
  ["default-scheduler", "The part of Kubernetes that decides which talk each hour goes to."],
  ["Scheduled", "Kubernetes found a talk for this hour and gave you a seat."],
  ["FailedScheduling", "Kubernetes couldn't find a talk for this hour yet."],
  ["Taint", "A sign on a talk that says what you need to know before you go. Each concept a talk assumes is one taint."],
  ["Toleration", "A concept you already know. A pod can only go to a talk whose taints it tolerates."],
  ["untolerated taint(s)", "The talk assumes a concept you haven't learned yet."],
];

function slotLabel(slot) {
  const [day, hhmm] = slot.split("-");
  if (!DAYS[day] || !/^\d{4}$/.test(hhmm || "")) return slot;
  const h = Number(hhmm.slice(0, 2));
  return `${DAYS[day]} ${((h + 11) % 12) + 1}:${hhmm.slice(2)} ${h < 12 ? "a.m." : "p.m."}`;
}

function sentence(p) {
  if (p.phase === "Running" || p.node) {
    return p.title ? `You have a seat in “${p.title}”.` : "You have a seat in a talk this hour.";
  }
  return p.plain || "Kubernetes hasn't looked at this hour yet.";
}

function Row({ label, word, children }) {
  return (
    <>
      <dt style={{ fontWeight: 600 }}>
        <a href={`#k8s-word-${word}`} aria-describedby={`k8s-word-${word}`} style={{ color: "inherit" }}>{label}</a>
      </dt>
      <dd style={{ margin: "0 0 0.4rem 0" }}>{children}</dd>
    </>
  );
}

export default function KubernetesView({ classId, student }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const panel = useId();

  useEffect(() => {
    if (!open) return undefined;
    let stop = false;
    const load = () =>
      fetch(`/api/placements?class=${encodeURIComponent(classId)}&student=${encodeURIComponent(student)}`)
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then((d) => { if (!stop) { setData(d.placements); setError(null); } })
        .catch(() => !stop && setError("Your week isn't available right now. Try again in a moment."));
    load();
    const timer = setInterval(load, 5000);
    return () => { stop = true; clearInterval(timer); };
  }, [open, classId, student]);

  return (
    <section style={{ marginTop: "2rem" }}>
      <button type="button" aria-expanded={open} aria-controls={panel} onClick={() => setOpen(!open)}
              style={{ font: "inherit", padding: "0.5rem 0.9rem", borderRadius: 6, border: "1px solid #3b6fb6",
                       background: open ? "#3b6fb6" : "#fff", color: open ? "#fff" : "#3b6fb6", cursor: "pointer" }}>
        {open ? "Hide" : "Show"} my week as Kubernetes sees it
      </button>
      <div id={panel} hidden={!open}>
        {error && <p role="alert">{error}</p>}
        {open && data && data.length === 0 && <p>You don't have any hours placed yet.</p>}
        {open && data && data.map((p) => (
          <article key={p.slot} aria-label={slotLabel(p.slot)}
                   style={{ border: "1px solid #ccd", borderRadius: 8, padding: "0.8rem 1rem", margin: "1rem 0" }}>
            <h3 style={{ margin: "0 0 0.5rem" }}>{slotLabel(p.slot)}</h3>
            <dl style={{ margin: 0 }}>
              <Row label="Pod" word="Pod"><code>{p.pod || "—"}</code></Row>
              <Row label="Phase" word="Phase"><code>{p.phase}</code></Row>
              <Row label="Node" word="Node"><code>{p.node || "No talk yet"}</code></Row>
              <Row label="Latest event" word="Event">
                {p.event
                  ? <code style={{ whiteSpace: "pre-wrap" }}>{[p.event.type, p.event.reason, p.event.from, p.event.message].filter(Boolean).join("  ")}</code>
                  : "None yet"}
              </Row>
            </dl>
            <p style={{ margin: "0.6rem 0 0", fontSize: "1.05rem" }}>{sentence(p)}</p>
          </article>
        ))}
        <h3>What these Kubernetes words mean</h3>
        <dl>
          {WORDS.map(([word, meaning]) => (
            <div key={word} style={{ marginBottom: "0.4rem" }}>
              <dt style={{ display: "inline", fontWeight: 600 }}><code>{word}</code>: </dt>
              <dd id={`k8s-word-${word}`} style={{ display: "inline", margin: 0 }}>{meaning}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}
