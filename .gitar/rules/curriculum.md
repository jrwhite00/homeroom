---
title: "Curriculum check"
description: "Check new or changed courses against the concept table, in plain language for teachers"
type: check
when: "A PR adds or changes a file in curriculum/courses/"
actions: "Check each changed course file and post one comment a teacher can read: list each problem, quote the line it is on and say how to fix it, or say the course looks ready for a teacher to approve"
---

Course files are written and approved by teachers, not programmers. Each file in curriculum/courses/ is one course, as simple `name: value` lines.

Check every changed file in curriculum/courses/:

1. Every concept listed in `teaches` has a matching file in curriculum/concepts/. For example, `containers` needs curriculum/concepts/containers.yaml. Name any concept that has no file.
2. A course lists at most 4 concepts in `teaches`. One course rarely teaches more, so more than 4 usually means the match was too generous.
3. `standard` is filled in, naming the state or national standard the course comes from.
4. The file holds no student names, emails, ages, schools or other personal details. Courses describe classes, never people.

Write the comment for a teacher: plain words, no code terms. Quote the line that has the problem and say what to change. If every check passes, say "This course looks ready for a teacher to approve."

Never approve, merge or push changes to course files yourself. A teacher decides.
