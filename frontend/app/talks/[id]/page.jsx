"use client";

import { use, useEffect, useState } from "react";
import Decoded from "../../Decoded";

// One talk, with its description run through the jargon decoder.
export default function TalkPage({ params }) {
  const { id } = use(params);
  const [talk, setTalk] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch(`/api/talks/${encodeURIComponent(id)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status === 404 ? "We couldn't find that talk." : "Something went wrong. Try again.")))
      .then(setTalk)
      .catch((e) => setError(typeof e === "string" ? e : "Something went wrong. Try again."));
  }, [id]);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 720, margin: "3rem auto", padding: "0 1rem", lineHeight: 1.6 }}>
      {error && <p role="alert">{error}</p>}
      {!talk && !error && <p>Loading…</p>}
      {talk && (
        <article>
          <h1>{talk.title}</h1>
          <p>
            Words with a dotted underline are explained by your teachers. Point at one, or move to it with the Tab key.
          </p>
          <p><Decoded segments={talk.abstract} /></p>
        </article>
      )}
    </main>
  );
}
