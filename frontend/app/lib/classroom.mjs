// Who is in which talk, from GET /api/placements?class=. Pure functions, so they can be tested.

const key = (p) => `${p.student}:${p.slot}`;
const seated = (p) => p.phase === "Running" && Boolean(p.talk);

// One entry per hour, each with its talks and the students in them. Students without a seat
// (waiting, or being set up again after their talk was cancelled) are in a talk of null, listed last.
export function roomsByHour(placements) {
  const hours = new Map();
  for (const p of placements) {
    if (!hours.has(p.slot)) hours.set(p.slot, new Map());
    const talks = hours.get(p.slot);
    const id = seated(p) ? p.talk : null;
    if (!talks.has(id)) talks.set(id, { talk: id, title: id ? p.title || id : null, students: [] });
    talks.get(id).students.push(p);
  }
  return [...hours]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([slot, talks]) => ({
      slot,
      talks: [...talks.values()]
        .map((t) => ({ ...t, students: [...t.students].sort((a, b) => a.student.localeCompare(b.student)) }))
        .sort((a, b) => (a.talk === null) - (b.talk === null) || a.title?.localeCompare(b.title ?? "") || 0),
    }));
}

// Remembers each student's last talk and reports who was moved to a different one.
// seen: { "eli:thu-1100": { talk, title } } from the last call. A student who is between talks
// (their pod was evicted and is being set up again) keeps their old talk in `seen`, so the move
// is reported once they are seated again, even if no refresh caught them waiting.
export function track(seen, placements) {
  const next = { ...seen };
  const moves = [];
  for (const p of placements) {
    if (!seated(p)) continue;
    const before = seen[key(p)];
    if (before && before.talk !== p.talk) {
      moves.push({ key: key(p), student: p.student, slot: p.slot, from: before.talk, fromTitle: before.title,
                   to: p.talk, toTitle: p.title || p.talk });
    }
    next[key(p)] = { talk: p.talk, title: p.title || p.talk };
  }
  return { seen: next, moves };
}

// The talk a student who isn't seated right now was last seen in, if any.
export function lastTalk(seen, p) {
  return seated(p) ? null : seen[key(p)] || null;
}
