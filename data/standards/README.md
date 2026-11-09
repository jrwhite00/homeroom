# Curriculum standards

Homeroom indexes published computer science standards into the Elasticsearch `standards` index,
one document per standard, so concepts and courses can be lined up with what students are
expected to learn.

**No standards text is kept in this repo.** The repo is public, and the CSTA standards are licensed
CC BY-NC-SA 4.0. `python -m app.load_standards` downloads each PDF into `data/standards/cache/`
(ignored by git), checks it against a pinned SHA-256, extracts the text with pypdf, and parses it.

| Source | Standards | Publisher's document |
| --- | ---: | --- |
| `csta-2026-pk12-cs-standards` | 331 | [2026 CSTA PK-12 Computer Science Standards](https://csteachers.org/pk12standards/). Download it in a browser (the site blocks scripted downloads) and save it as `data/standards/cache/csta-2026-pk12-cs-standards.pdf`. |
| `utah-6-12-cs-standards` | 51 | [Utah 6-12 Computer Science Standards](https://schools.utah.gov/cte/_cte/computer/612ComputerScienceStandards.pdf) |
| `utah-cte-computer-programming-1` | 17 | [Computer Programming 1 strands and standards](https://schools.utah.gov/cte/_cte/strands/ComputerProgramming1.pdf) |
| `utah-cte-computer-programming-2` | 17 | [Computer Programming 2 strands and standards](https://schools.utah.gov/cte/_cte/strands/ComputerProgramming2.pdf) |
| `utah-cte-computer-science-principles` | 20 | [Computer Science Principles strands and standards](https://www.schools.utah.gov/cte/_cte/strands/ComputerSciencePrinciples.pdf) |
| `utah-cte-exploring-computer-science` | 26 | [Exploring Computer Science strands and standards](https://schools.utah.gov/cte/_cte/strands/ExploringComputerScience.pdf) |
| `utah-cte-web-development-1` | 18 | [Web Development 1 strands and standards](https://schools.utah.gov/cte/_cte/strands/WebDevelopment1.pdf) |

What counts as one standard:

- CSTA: each grade-band statement, for example `MS-ALG-PS-01`, with its boundary statement.
- Utah 6-12: each coded standard, for example `9/10.AP.1`, with its explanation.
- Utah CTE: each standard within a strand, coded `strand.standard`, for example `2.1`, with its
  bullets. Performance skills, test-point tables and vocabulary lists are not standards.

If a publisher changes a document, its hash no longer matches and the loader stops. Check the
parser against the new PDF, then update the hash and expected count in `SOURCES` in
`backend/app/load_standards.py` in a pull request.

## Credit

- Computer Science Teachers Association (2026). *2026 CSTA PK-12 computer science standards.*
  Licensed [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).
- Utah State Board of Education: Utah 6-12 Computer Science Standards (2019) and the CTE strands
  and standards documents linked above.
