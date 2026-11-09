import assert from "node:assert/strict";
import test from "node:test";
import { lastTalk, roomsByHour, track } from "./classroom.mjs";

const at = (student, talk, phase = "Running", slot = "thu-1100") =>
  ({ student, slot, phase, talk: phase === "Running" ? talk : null, title: talk ? `Title ${talk}` : null });

test("students are grouped by hour and talk, waiting students last", () => {
  const hours = roomsByHour([at("gia", "t2"), at("eli", "t2"), at("hal", "t1"), at("fin", null, "Pending"),
                             at("maya", "t9", "Running", "thu-1245")]);
  assert.deepEqual(hours.map((h) => h.slot), ["thu-1100", "thu-1245"]);
  const talks = hours[0].talks;
  assert.deepEqual(talks.map((t) => t.talk), ["t1", "t2", null]);
  assert.deepEqual(talks[1].students.map((p) => p.student), ["eli", "gia"]);
  assert.equal(talks[1].title, "Title t2");
  assert.deepEqual(talks[2].students.map((p) => p.student), ["fin"]);
});

test("a student moved to another talk is reported once", () => {
  let { seen, moves } = track({}, [at("eli", "t1"), at("hal", "t2")]);
  assert.deepEqual(moves, []);
  ({ seen, moves } = track(seen, [at("eli", "t2"), at("hal", "t2")]));
  assert.deepEqual(moves, [{ key: "eli:thu-1100", student: "eli", slot: "thu-1100", from: "t1", fromTitle: "Title t1",
                             to: "t2", toTitle: "Title t2" }]);
  ({ moves } = track(seen, [at("eli", "t2"), at("hal", "t2")]));
  assert.deepEqual(moves, []);
});

test("a move is still reported when a refresh caught the student between talks", () => {
  let { seen } = track({}, [at("eli", "t1")]);
  const waiting = at("eli", null, "Missing");
  let moves;
  ({ seen, moves } = track(seen, [waiting]));
  assert.deepEqual(moves, []);
  assert.deepEqual(lastTalk(seen, waiting), { talk: "t1", title: "Title t1" });
  ({ moves } = track(seen, [at("eli", "t3")]));
  assert.equal(moves[0].from, "t1");
  assert.equal(moves[0].to, "t3");
});

test("a seated student has no last talk to show", () => {
  const { seen } = track({}, [at("eli", "t1")]);
  assert.equal(lastTalk(seen, at("eli", "t1")), null);
});
