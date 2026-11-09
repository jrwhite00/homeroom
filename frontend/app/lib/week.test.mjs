import assert from "node:assert/strict";
import test from "node:test";

import { byDay, coursesFor, dayKey, dayTabs, onlyDay, proposalBody, proposalMessage, readQuery, statesFrom, timeOf, weekQuery } from "./week.mjs";

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

test("a talk's day is its Denver calendar day", () => {
  assert.equal(dayKey("2026-11-10T09:00:00-07:00"), "2026-11-10");
  assert.equal(dayKey("2026-11-11T03:00:00Z"), "2026-11-10"); // 8 pm Tuesday in Denver, already Wednesday in UTC
  assert.equal(dayKey(undefined), "");
});

const WEEK = {
  ready: [{ id: "a", starts_at: "2026-11-11T10:00:00-07:00" }, { id: "b", starts_at: "2026-11-10T09:00:00-07:00" },
          { id: "c", starts_at: "2026-11-10T11:00:00-07:00" }],
  one_step: [{ id: "d", starts_at: "2026-11-12T10:00:00-07:00" }],
  not_yet: [],
  unknown: [{ id: "e" }],
};

test("there is one tab per day, in date order, counting the ready talks", () => {
  const tabs = dayTabs(WEEK);
  assert.deepEqual(tabs.map((t) => [t.key, t.ready]), [["2026-11-10", 2], ["2026-11-11", 1], ["2026-11-12", 0]]);
  assert.equal(tabs[0].label, "Tue 10");
  assert.equal(tabs[0].month, "November");
  assert.deepEqual(dayTabs({ ready: [], one_step: [], not_yet: [], unknown: [{ id: "e" }] }), []); // no times, no tabs
});

test("choosing a day keeps that day's talks in every group", () => {
  const day = onlyDay(WEEK, "2026-11-10");
  assert.deepEqual(day.ready.map((t) => t.id), ["b", "c"]);
  assert.deepEqual(day.one_step, []);
  assert.deepEqual(day.unknown, []); // a talk with no time is only under "All days"
  assert.equal(onlyDay(WEEK, ""), WEEK);
});
