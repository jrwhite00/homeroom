// Pure helpers for the student page. No network calls, so `npm test` can check them with node --test.

export const TIME_ZONE = "America/Denver";
export const GROUP_ORDER = ["ready", "one_step", "not_yet", "unknown"];
export const GROUP_HELP = {
  ready: "You already know every concept these talks expect.",
  one_step: "You're missing one concept. We suggest a way to pick it up first.",
  not_yet: "You're missing two or more concepts. Save these for later.",
  unknown: "We haven't checked yet which concepts these talks expect.",
};
export const MAX_PROPOSAL_CONCEPTS = 4; // the curriculum allows at most 4 concepts per course
export const PROPOSAL_REASON = "A student said this course taught it.";

export function statesFrom(courses) {
  return [...new Set(courses.map((c) => c.state).filter(Boolean))].sort();
}

export function coursesFor(courses, state) {
  return courses.filter((c) => c.state === state);
}

function splitList(value) {
  return [...new Set((value || "").split(",").map((s) => s.trim()).filter(Boolean))];
}

// The page's choices live in the address, so the back button and a refresh keep them.
// Only a state and course and concept slugs: nothing about the student.
export function weekQuery({ state, courses = [], concepts = [] }) {
  const params = new URLSearchParams();
  if (state) params.set("state", state);
  if (courses.length) params.set("courses", courses.join(","));
  if (concepts.length) params.set("concepts", concepts.join(","));
  return params.toString();
}

export function readQuery(search) {
  const params = new URLSearchParams(search);
  return { state: params.get("state") || "", courses: splitList(params.get("courses")),
           concepts: splitList(params.get("concepts")) };
}

const dayFormat = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, weekday: "long", month: "long", day: "numeric" });
const timeFormat = new Intl.DateTimeFormat("en-US", { timeZone: TIME_ZONE, hour: "numeric", minute: "2-digit" });

export function dayOf(iso) {
  return iso ? dayFormat.format(new Date(iso)) : "Time not set";
}

export function timeOf(iso) {
  return iso ? timeFormat.format(new Date(iso)) : "";
}

// Talks in time order, split by day (Denver time). Talks without a time come last.
export function byDay(talks) {
  const sorted = [...talks].sort((a, b) => {
    if (!a.starts_at || !b.starts_at) return a.starts_at ? -1 : b.starts_at ? 1 : 0;
    return new Date(a.starts_at) - new Date(b.starts_at);
  });
  const days = [];
  for (const talk of sorted) {
    const day = dayOf(talk.starts_at);
    if (days.at(-1)?.day !== day) days.push({ day, talks: [] });
    days.at(-1).talks.push(talk);
  }
  return days;
}

export function proposalBody({ state, match, concepts }) {
  return { state, course: match.course, standard: match.standard,
           teaches: concepts.slice(0, MAX_PROPOSAL_CONCEPTS).map((slug) => ({ slug, reason: PROPOSAL_REASON })) };
}

// What to tell the student after POST /courses/propose, by its status.
export function proposalMessage(result, course) {
  switch (result?.status) {
    case "opened":
    case "already_open":
      return `Thanks. We asked a teacher to add ${course}. Until they do, the concepts you ticked still count.`;
    case "saved_locally":
      return `Thanks. We saved ${course} for a teacher to add. Until they do, the concepts you ticked still count.`;
    case "already_known":
      return `A teacher has already added ${course}. Choose it from the course list.`;
    default:
      return result?.message || "We couldn't send your course to a teacher right now. The concepts you ticked still count.";
  }
}
