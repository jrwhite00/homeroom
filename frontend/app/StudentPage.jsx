"use client";

import { useEffect, useRef, useState } from "react";

import {
  byDay, coursesFor, GROUP_HELP, GROUP_ORDER, MAX_PROPOSAL_CONCEPTS, proposalBody, proposalMessage, readQuery,
  statesFrom, timeOf, weekQuery,
} from "./lib/week.mjs";
import Decoded from "./Decoded";
import KubeView from "./KubeView";
import s from "./student.module.css";

// The student page: pick a state, tick courses, see the week. No accounts and nothing about the student
// is sent or stored; the address holds only the state and course and concept slugs.

async function getJson(path, options) {
  const r = await fetch(`/api${path}`, options);
  const body = await r.json().catch(() => null);
  if (!r.ok) throw new Error(body?.detail && typeof body.detail === "string" ? body.detail : "Something went wrong. Please try again.");
  return body;
}

export default function StudentPage() {
  const [allCourses, setAllCourses] = useState([]);
  const [concepts, setConcepts] = useState([]);
  const [state, setState] = useState("");
  const [picked, setPicked] = useState([]); // course slugs
  const [ticked, setTicked] = useState([]); // concept slugs from the checklist
  const [loadError, setLoadError] = useState("");
  const [week, setWeek] = useState(null);
  const [weekError, setWeekError] = useState("");
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState(""); // read out by screen readers
  const weekHeading = useRef(null);

  const conceptName = (slug) => concepts.find((c) => c.slug === slug)?.concept || slug;

  async function showWeek(choice) {
    setLoading(true);
    setWeekError("");
    setStatus("Finding your week…");
    const query = weekQuery(choice);
    window.history.replaceState(null, "", `?${query}`);
    try {
      const body = await getJson(`/week?${query}`);
      setWeek(body);
      setStatus(`Your week is ready: ${body.counts.ready} talks you're ready for.`);
      requestAnimationFrame(() => weekHeading.current?.focus());
    } catch (e) {
      setWeekError(e.message);
      setStatus(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const fromAddress = readQuery(window.location.search);
    Promise.all([getJson("/courses"), getJson("/concepts")])
      .then(([c, k]) => {
        setAllCourses(c.courses);
        setConcepts(k.concepts);
        const states = statesFrom(c.courses);
        if (fromAddress.state && states.includes(fromAddress.state)) {
          setState(fromAddress.state);
          setPicked(fromAddress.courses);
          setTicked(fromAddress.concepts);
          showWeek(fromAddress);
        }
      })
      .catch((e) => setLoadError(e.message));
  }, []);

  const states = statesFrom(allCourses);
  const stateCourses = coursesFor(allCourses, state);

  function toggle(list, setList, slug) {
    setList(list.includes(slug) ? list.filter((x) => x !== slug) : [...list, slug]);
  }

  function onStateChange(e) {
    setState(e.target.value);
    setPicked([]);
    setWeek(null);
  }

  function onSubmit(e) {
    e.preventDefault();
    if (!state) {
      setWeekError("Choose your state first.");
      return;
    }
    showWeek({ state, courses: picked, concepts: ticked });
  }

  return (
    <main className={s.main}>
      <h1>Homeroom</h1>
      <p className={s.lead}>Pick your state and the classes you&apos;ve taken. We&apos;ll sort this week&apos;s KubeCon talks
        by whether you&apos;re ready for them.</p>
      <details className={s.decoder}>
        <summary>What&apos;s a concept?</summary>
        <p>A concept is one idea a talk expects you to know already, like &quot;containers&quot; (a way to package an
          app so it runs the same everywhere). Your classes teach concepts. We compare what each talk expects with
          what your classes taught.</p>
      </details>

      {loadError && <p className={s.error} role="alert">{loadError}</p>}

      <form onSubmit={onSubmit} className={s.form} aria-describedby="privacy">
        <p id="privacy" className={s.small}>No account and no name needed. We don&apos;t save anything about you.</p>
        <div className={s.field}>
          <label htmlFor="state">Your state</label>
          <select id="state" value={state} onChange={onStateChange}>
            <option value="">Choose your state</option>
            {states.map((st) => <option key={st} value={st}>{st}</option>)}
          </select>
        </div>

        {state && (
          <fieldset className={s.fieldset}>
            <legend>Classes you&apos;ve taken in {state}</legend>
            {stateCourses.map((c) => (
              <div key={c.slug} className={s.check}>
                <input type="checkbox" id={`course-${c.slug}`} checked={picked.includes(c.slug)}
                       onChange={() => toggle(picked, setPicked, c.slug)} />
                <label htmlFor={`course-${c.slug}`}>{c.course}</label>
              </div>
            ))}
            {ticked.length > 0 && (
              <div className={s.ticked}>
                <p>Concepts you added yourself:</p>
                <ul>
                  {ticked.map((slug) => (
                    <li key={slug}>
                      {conceptName(slug)}{" "}
                      <button type="button" className={s.linkButton} onClick={() => toggle(ticked, setTicked, slug)}>
                        Remove<span className={s.visuallyHidden}> {conceptName(slug)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </fieldset>
        )}

        <button type="submit" className={s.primary} disabled={loading}>
          {loading ? "Finding your week…" : "Show my week"}
        </button>
        {weekError && <p className={s.error} role="alert">{weekError}</p>}
      </form>

      {state && (
        <NotOnTheList key={state} state={state} concepts={concepts}
                      onKnownCourse={(m) => { if (!picked.includes(m.slug)) setPicked([...picked, m.slug]);
                                              setStatus(`Added ${m.course} to your classes.`); }}
                      onConcepts={(slugs) => { setTicked([...new Set([...ticked, ...slugs])]);
                                               setStatus(`Added ${slugs.length} concepts.`); }} />
      )}

      <p role="status" aria-live="polite" className={s.visuallyHidden}>{status}</p>

      {week && <Week week={week} headingRef={weekHeading} />}
    </main>
  );
}

function NotOnTheList({ state, concepts, onKnownCourse, onConcepts }) {
  const [text, setText] = useState("");
  const [matches, setMatches] = useState(null);
  const [choice, setChoice] = useState("");
  const [step, setStep] = useState("search"); // search | propose | fallback
  const [chosen, setChosen] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const resultsHeading = useRef(null);

  function reset() {
    setText(""); setMatches(null); setChoice(""); setStep("search"); setChosen(null);
  }

  async function find(e) {
    e.preventDefault();
    setError(""); setMessage(""); setChoice("");
    if (!text.trim()) { setError("Type the name of your class first."); return; }
    setBusy(true);
    try {
      const body = await getJson("/courses/match", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ state, text: text.trim() }),
      });
      setMatches(body.matches);
      setStep("search");
      requestAnimationFrame(() => resultsHeading.current?.focus());
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  function confirm(e) {
    e.preventDefault();
    setError("");
    if (choice === "") { setError("Choose one of the classes, or \"None of these\"."); return; }
    if (choice === "none") { setStep("fallback"); return; }
    const m = matches[Number(choice)];
    if (m.known) {
      onKnownCourse(m);
      setMessage(`Added ${m.course} to your classes.`);
      reset();
      return;
    }
    setChosen(m);
    setStep("propose");
  }

  async function sendProposal(slugs) {
    setBusy(true);
    try {
      const result = await getJson("/courses/propose", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(proposalBody({ state, match: chosen, concepts: slugs })),
      });
      setMessage(proposalMessage(result, chosen.course));
    } catch (err) {
      setMessage(proposalMessage({ status: "error", message: err.message }, chosen.course));
    } finally {
      setBusy(false);
    }
    onConcepts(slugs);
    reset();
  }

  return (
    <section className={s.panel} aria-labelledby="not-listed">
      <h2 id="not-listed">Not on the list? Type it</h2>
      <form onSubmit={find} className={s.inline}>
        <div className={s.field}>
          <label htmlFor="class-name">Name of your class</label>
          <input id="class-name" type="text" value={text} maxLength={120} autoComplete="off"
                 onChange={(e) => setText(e.target.value)} aria-describedby="class-hint" />
          <p id="class-hint" className={s.small}>For example &quot;web design&quot; or &quot;Computer Programming 2&quot;.
            Please don&apos;t type your name.</p>
        </div>
        <button type="submit" disabled={busy}>Find my class</button>
      </form>

      {matches && step === "search" && (
        <form onSubmit={confirm}>
          <fieldset className={s.fieldset}>
            <legend ref={resultsHeading} tabIndex={-1}>
              {matches.length ? "Is it one of these?" : "We didn't find a close class."}
            </legend>
            {matches.map((m, i) => (
              <div key={m.course} className={s.check}>
                <input type="radio" name="match" id={`match-${i}`} value={String(i)} checked={choice === String(i)}
                       onChange={(e) => setChoice(e.target.value)} />
                <label htmlFor={`match-${i}`}>{m.course}{m.known ? "" : " (we haven't added this class yet)"}</label>
              </div>
            ))}
            <div className={s.check}>
              <input type="radio" name="match" id="match-none" value="none" checked={choice === "none"}
                     onChange={(e) => setChoice(e.target.value)} />
              <label htmlFor="match-none">None of these. Let me pick concepts instead.</label>
            </div>
          </fieldset>
          <button type="submit">That&apos;s my class</button>
        </form>
      )}

      {step === "propose" && chosen && (
        <ConceptChecklist concepts={concepts} max={MAX_PROPOSAL_CONCEPTS} busy={busy}
                          legend={`Which concepts did ${chosen.course} teach you? Tick up to ${MAX_PROPOSAL_CONCEPTS}.`}
                          note="We'll ask a teacher to add this class. Until they do, the concepts you tick still count."
                          button="Add the class" onDone={sendProposal} onCancel={reset} />
      )}

      {step === "fallback" && (
        <ConceptChecklist concepts={concepts} busy={busy} legend="Tick the concepts you've learned."
                          button="Add these concepts" onDone={(slugs) => { onConcepts(slugs); reset();
                                                                           setMessage(`Added ${slugs.length} concepts.`); }}
                          onCancel={reset} />
      )}

      {error && <p className={s.error} role="alert">{error}</p>}
      {message && <p className={s.note}>{message}</p>}
    </section>
  );
}

function ConceptChecklist({ concepts, legend, note, button, max, busy, onDone, onCancel }) {
  const [chosen, setChosen] = useState([]);
  const [error, setError] = useState("");
  const legendRef = useRef(null);
  useEffect(() => { legendRef.current?.focus(); }, []);

  function toggle(slug) {
    setError("");
    if (chosen.includes(slug)) setChosen(chosen.filter((x) => x !== slug));
    else if (max && chosen.length >= max) setError(`You can tick up to ${max}.`);
    else setChosen([...chosen, slug]);
  }

  function submit(e) {
    e.preventDefault();
    if (!chosen.length) { setError("Tick at least one concept."); return; }
    onDone(chosen);
  }

  return (
    <form onSubmit={submit}>
      <fieldset className={s.fieldset}>
        <legend ref={legendRef} tabIndex={-1}>{legend}</legend>
        {note && <p className={s.small}>{note}</p>}
        {concepts.map((c) => (
          <div key={c.slug} className={s.check}>
            <input type="checkbox" id={`concept-${c.slug}`} checked={chosen.includes(c.slug)}
                   onChange={() => toggle(c.slug)} aria-describedby={`concept-${c.slug}-def`} />
            <label htmlFor={`concept-${c.slug}`}>{c.concept}</label>
            <span id={`concept-${c.slug}-def`} className={s.definition}>{c.definition}</span>
          </div>
        ))}
      </fieldset>
      {error && <p className={s.error} role="alert">{error}</p>}
      <div className={s.buttons}>
        <button type="submit" disabled={busy}>{button}</button>
        <button type="button" className={s.linkButton} onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

function Week({ week, headingRef }) {
  return (
    <section className={s.week} aria-labelledby="week-heading">
      <h2 id="week-heading" ref={headingRef} tabIndex={-1}>Your week</h2>
      <p>
        {week.knows.length
          ? <>Concepts you know: {week.knows.join(", ")}.</>
          : <>You haven&apos;t added any classes or concepts yet, so every talk that expects a concept is &quot;not yet&quot;.</>}
      </p>
      {week.messages.map((m) => <p key={m} className={s.note}>{m}</p>)}
      {GROUP_ORDER.map((g) => (
        <details key={g} className={s.group} open={g === "ready"}>
          <summary>
            <h3 className={s.groupTitle}>{week.labels[g]} <span className={s.count}>({week.counts[g]} talks)</span></h3>
          </summary>
          <p className={s.small}>{GROUP_HELP[g]}</p>
          {week.groups[g].length === 0 && <p>No talks here.</p>}
          {byDay(week.groups[g]).map(({ day, talks }) => (
            <div key={day}>
              <h4 className={s.day}>{day}</h4>
              <ul className={s.talks}>
                {talks.map((t) => <Talk key={t.id} talk={t} group={g} />)}
              </ul>
            </div>
          ))}
        </details>
      ))}
      <p className={s.small}>Times are Mountain Time (Salt Lake City). Open &quot;What&apos;s this talk about?&quot; to read
        a talk&apos;s description, with technical words explained.</p>
      <p className={s.small}>Below is an example: how Kubernetes places the week of Maya, a made-up demo student. It isn&apos;t your week.</p>
      <KubeView classId="demo" student="maya" />
    </section>
  );
}

// The talk's description, run through the jargon decoder. Loaded only when the student opens it, so
// a week of hundreds of talks doesn't ask for hundreds of descriptions up front.
function About({ id }) {
  const [talk, setTalk] = useState(null);
  const [error, setError] = useState(null);
  const asked = useRef(false);
  const onToggle = (e) => {
    if (!e.currentTarget.open || asked.current) return;
    asked.current = true;
    setError(null);
    fetch(`/api/talks/${encodeURIComponent(id)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setTalk)
      .catch(() => { asked.current = false; setError("We couldn't load this description. Close and open it to try again."); });
  };
  return (
    <details className={s.about} onToggle={onToggle}>
      <summary>What&apos;s this talk about?</summary>
      {error && <p role="alert">{error}</p>}
      {!talk && !error && <p>Loading…</p>}
      {talk && (talk.abstract.length
        ? <p>Words with a dotted underline are explained by your teachers.{" "}<Decoded segments={talk.abstract} /></p>
        : <p>This talk has no description.</p>)}
    </details>
  );
}

function Talk({ talk, group }) {
  const sg = talk.suggestion;
  return (
    <li className={s.talk}>
      <span className={s.time}>{timeOf(talk.starts_at)}</span>{" "}
      <strong><a href={`/talks/${encodeURIComponent(talk.id)}`} className={s.talkLink}>{talk.title}</a></strong>
      {talk.room && <span className={s.room}> · {talk.room}</span>}
      {talk.missing.length > 0 && (
        <p className={s.missing}>You&apos;d need: {talk.missing.join(", ")}.</p>
      )}
      <About id={talk.id} />
      {group === "one_step" && sg?.kind === "beginner_talk" && (
        <p className={s.suggest}>Learn {sg.covers} first: &quot;{sg.title}&quot; at {timeOf(sg.starts_at)}
          {sg.room ? `, ${sg.room}` : ""}.</p>
      )}
      {group === "one_step" && sg?.kind === "project_booth" && (
        <p className={s.suggest}>Learn {sg.covers} first: visit the {sg.project} booth
          {sg.kiosk ? ` (${sg.kiosk})` : ""}{sg.room ? ` in ${sg.room}` : ""}.</p>
      )}
    </li>
  );
}
