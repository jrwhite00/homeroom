# Curriculum standards

Plain-text copies of published computer science standards. Homeroom indexes them into the
Elasticsearch `standards` index, one document per standard, so concepts and courses can be
lined up with what students are expected to learn.

The text was extracted from each publisher's PDF with pypdf on Nov 8, 2026. Apart from that
extraction, the text is unchanged. Page headers, footers and doubled headings that the
extraction produced are left in the files and skipped by the parser
(`backend/app/standards.py`).

Load or reload the index with `cd backend && python -m app.load_standards --recreate`
(see the Commands section of CLAUDE.md).

| File | Standards | Source |
| --- | ---: | --- |
| `csta-2026-pk12-cs-standards.txt` | 331 | [CSTA 2026 PK-12 Computer Science Standards](https://csteachers.org/k12standards/) |
| `utah-6-12-cs-standards.txt` | 51 | [Utah 6-12 Computer Science Standards](https://schools.utah.gov/cte/_cte/computer/612ComputerScienceStandards.pdf), Utah State Board of Education, 2019 |
| `utah-cte-computer-science-principles.txt` | 20 | [Computer Science Principles strands and standards](https://www.schools.utah.gov/cte/_cte/strands/ComputerSciencePrinciples.pdf), Utah State Board of Education |
| `utah-cte-exploring-computer-science.txt` | 26 | [Exploring Computer Science strands and standards](https://schools.utah.gov/cte/_cte/strands/ExploringComputerScience.pdf), Utah State Board of Education |
| `utah-cte-computer-programming-1.txt` | 17 | [Computer Programming 1 strands and standards](https://schools.utah.gov/cte/_cte/strands/ComputerProgramming1.pdf), Utah State Board of Education |
| `utah-cte-computer-programming-2.txt` | 17 | [Computer Programming 2 strands and standards](https://schools.utah.gov/cte/_cte/strands/ComputerProgramming2.pdf), Utah State Board of Education |
| `utah-cte-web-development-1.txt` | 18 | [Web Development 1 strands and standards](https://schools.utah.gov/cte/_cte/strands/WebDevelopment1.pdf), Utah State Board of Education |

What counts as one standard:

- CSTA: each grade-band statement, for example `MS-ALG-PS-01`, with its boundary statement.
  Grade bands are EK, E1 to E5, MS, HS and the high school specialty levels S1 and S2.
- Utah 6-12: each coded standard, for example `9/10.AP.1`, with its explanation.
- Utah CTE: each standard within a strand, coded `strand.standard`, for example `2.1`, with
  its bullets. Performance skills, test-point tables and vocabulary lists are not standards.

## Credit and licenses

**CSTA.** Computer Science Teachers Association (2026). *2026 CSTA PK-12 computer science
standards.* Licensed under
[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/): attribution,
non-commercial use only, and adaptations shared under the same license. The text file here is
an extraction of the CSTA PDF and is shared under the same license.

**Utah.** Utah 6-12 Computer Science Standards and the CTE strands and standards documents are
published by the Utah State Board of Education. They are credited here with links to the
original documents.
