import assert from "node:assert/strict";
import test from "node:test";

import { byDay, coursesFor, proposalBody, proposalMessage, readQuery, statesFrom, timeOf, weekQuery } from "./week.mjs";

const COURSES = [
  { slug: "fx-b", course: "B course", state: "Utah" },
  { slug: "fx-a", course: "A course", state: "Utah" },
  { slug: "fx-o", course: "Ohio course", state: "Ohio" },
];

test("states are listed once, in order", () => {
  assert.deepEqual(statesFrom(COURSES), ["Ohio", "Utah"]);
  assert.deepEqual(coursesFor(COURSES, "Utah").map((c) => c.slug), ["fx-b", "fx-a"]);
});

test("the address holds only state, course and concept slugs", () => {
  const q = weekQuery({ state: "Utah", courses: ["fx-a", "fx-b"], concepts: ["containers"] });
  assert.equal(q, "state=Utah&courses=fx-a%2Cfx-b&concepts=containers");
  assert.deepEqual(readQuery("?" + q), { state: "Utah", courses: ["fx-a", "fx-b"], concepts: ["containers"] });
  assert.equal(weekQuery({ state: "Utah" }), "state=Utah");
  assert.deepEqual(readQuery(""), { state: "", courses: [], concepts: [] });
  assert.deepEqual(readQuery("?courses=fx-a,,fx-a, fx-b"), { state: "", courses: ["fx-a", "fx-b"], concepts: [] });
});

test("talks are grouped by day in Denver time", () => {
  const days = byDay([
    { title: "late", starts_at: "2026-11-11T09:00:00-07:00" },
    { title: "no time" },
    { title: "early", starts_at: "2026-11-10T09:00:00-07:00" },
    { title: "evening in UTC", starts_at: "2026-11-11T02:30:00Z" }, // 7:30 PM on the 10th in Denver
  ]);
  assert.deepEqual(days.map((d) => [d.day, d.talks.map((t) => t.title)]), [
    ["Tuesday, November 10", ["early", "evening in UTC"]],
    ["Wednesday, November 11", ["late"]],
    ["Time not set", ["no time"]],
  ]);
  assert.equal(timeOf("2026-11-11T02:30:00Z"), "7:30 PM");
});

test("a proposal carries the matched standard and at most four concepts", () => {
  const body = proposalBody({ state: "Utah", match: { course: "Web Development 1", standard: "Utah CTE Web Development 1 strands" },
                              concepts: ["a", "b", "c", "d", "e"] });
  assert.equal(body.course, "Web Development 1");
  assert.equal(body.standard, "Utah CTE Web Development 1 strands");
  assert.deepEqual(body.teaches.map((t) => t.slug), ["a", "b", "c", "d"]);
  assert.ok(body.teaches.every((t) => t.reason.length > 0));
  assert.deepEqual(Object.keys(body).sort(), ["course", "standard", "state", "teaches"]); // nothing about the student
});

test("every proposal result gets a plain message", () => {
  assert.match(proposalMessage({ status: "opened" }, "Web Development 1"), /asked a teacher to add Web Development 1/);
  assert.match(proposalMessage({ status: "already_known" }, "X"), /already added X/);
  assert.equal(proposalMessage({ status: "rate_limited", message: "Please wait a minute." }, "X"), "Please wait a minute.");
  assert.match(proposalMessage(null, "X"), /couldn't send/);
});
