import assert from "node:assert/strict";
import test from "node:test";

import { byDay, coursesFor, hasLevels, proposalBody, proposalMessage, readQuery, statesFrom, timeOf, weekQuery } from "./week.mjs";

const COURSES = [
  { slug: "fx-b", course: "B course", state: "Utah" },
  { slug: "fx-a", course: "A course", state: "Utah" },
  { slug: "fx-o", course: "Ohio course", state: "Ohio" },
];

test("states are listed once, in order", () => {
  assert.deepEqual(statesFrom(COURSES), ["Ohio", "Utah"]);
  assert.deepEqual(coursesFor(COURSES, "Utah").map((c) => c.slug), ["fx-b", "fx-a"]);
});

test("the checklist shows courses for the chosen level, plus courses for everyone", () => {
  const all = [
    { slug: "hs", course: "Programming 1", state: "Utah", level: "high-school" },
    { slug: "ug", course: "Programming 1", state: "Utah", level: "undergraduate" },
    { slug: "any", course: "Web design", state: "Utah", level: "" },
    { slug: "oh", course: "Ohio", state: "Ohio", level: "high-school" },
  ];
  assert.deepEqual(coursesFor(all, "Utah", "high-school").map((c) => c.slug), ["hs", "any"]);
  assert.deepEqual(coursesFor(all, "Utah", "undergraduate").map((c) => c.slug), ["ug", "any"]);
  assert.deepEqual(coursesFor(all, "Utah").map((c) => c.slug), ["hs", "ug", "any"]); // no level: all of them
});

test("the level menu is only needed when a course in the chosen state has a level", () => {
  const all = [
    { slug: "any", course: "Web design", state: "Utah", level: "" },
    { slug: "oh", course: "Ohio", state: "Ohio", level: "high-school" },
  ];
  assert.equal(hasLevels(all, "Utah"), false);
  assert.equal(hasLevels(all, "Ohio"), true);
  assert.equal(hasLevels([], "Utah"), false);
});

test("the address holds only state, level, course and concept slugs", () => {
  const q = weekQuery({ state: "Utah", courses: ["fx-a", "fx-b"], concepts: ["containers"] });
  assert.equal(q, "state=Utah&courses=fx-a%2Cfx-b&concepts=containers");
  assert.deepEqual(readQuery("?" + q), { state: "Utah", level: "", courses: ["fx-a", "fx-b"], concepts: ["containers"] });
  assert.equal(weekQuery({ state: "Utah", level: "graduate" }), "state=Utah&level=graduate");
  assert.equal(readQuery("?state=Utah&level=graduate").level, "graduate");
  assert.equal(weekQuery({ state: "Utah" }), "state=Utah");
  assert.deepEqual(readQuery(""), { state: "", level: "", courses: [], concepts: [] });
  assert.deepEqual(readQuery("?courses=fx-a,,fx-a, fx-b"), { state: "", level: "", courses: ["fx-a", "fx-b"], concepts: [] });
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
