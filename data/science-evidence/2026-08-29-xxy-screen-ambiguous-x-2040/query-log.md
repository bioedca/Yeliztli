# Query log — issue #2040, accessed 2026-08-29

All requests are unauthenticated GETs against public bibliographic endpoints, plus one MCP
tool call. No repository data, genotype, or sample identifier was transmitted.

| # | Tool / endpoint | Purpose | Outcome |
| --- | --- | --- | --- |
| 1 | Consensus MCP `search` | Required first rung; the 47,XXY karyotype definition | 20 papers; supplied both C1 sources |
| 2 | Scite MCP `search_literature` | Required first rung | **Unavailable** — monthly MCP quota exhausted 2026-08-27, service-reported reset 2026-09-03, not yet passed |
| 3 | PubMed MCP `search_articles` | Fallback for Scite: `Klinefelter syndrome 47,XXY karyotype review diagnosis` | 59 hits; 4 candidates retrieved |
| 4 | `GET .../esummary.fcgi?db=pubmed&id=39806878,39932051,33000452,29382506` | Confirm C1 candidates by title; check retraction flags; capture author lists for the independence check | HTTP 200 — two selected, both clean |
| 5 | `GET .../esummary.fcgi?db=pubmed&id=28035028` | Confirm the seXY citation already used across the codebase | HTTP 200 — title matches, no retraction flag |
| 6 | `GET .../esummary.fcgi?db=pubmed&id=38073250` | Confirm the second, independent C2 source | HTTP 200 — title matches, no retraction flag, disjoint authors from seXY |

Author lists were compared explicitly rather than assumed: C1's two reviews share no author,
and C2's two records share no author and no cohort.

No quota limit was reached on the PubMed endpoints; the one quota exhaustion in this packet is
Scite's, recorded above.
