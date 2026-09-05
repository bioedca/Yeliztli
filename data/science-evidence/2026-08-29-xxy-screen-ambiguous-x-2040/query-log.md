# Query log — issue #2040, accessed 2026-08-29

All requests are unauthenticated GETs against public bibliographic endpoints, plus one MCP tool
call. No repository data, genotype, or sample identifier was transmitted.

| # | Tool / endpoint | Purpose | Outcome |
| --- | --- | --- | --- |
| 1 | Consensus MCP `search` | Required first rung | 20 papers, all reviews; **not** used as C1 support |
| 2 | Scite MCP `search_literature` | Required first rung | **Unavailable** — monthly quota exhausted 2026-08-27, reset 2026-09-03, not yet passed |
| 3 | PubMed MCP `search_articles` | Locate the **primary** XXY description | resolved Jacobs & Strong 1959 → PMID:13632697 |
| 4 | PubMed MCP `search_articles` | Locate a second **primary** source: consecutive-newborn cytogenetic surveys | 2 hits; Hamerton 1975 (PMID:1183067) selected |
| 5 | `GET .../esummary.fcgi?db=pubmed&id=13632697,1183067` | Confirm both by title, journal, authors and publication type | HTTP 200 — both `Journal Article`, disjoint author lists |
| 6 | `GET .../elink.fcgi?dbfrom=pubmed&db=pubmed&id=13632697` | Retraction check via **linked relations** | HTTP 200 — no retraction/erratum/correction linkname |
| 7 | `GET .../elink.fcgi?dbfrom=pubmed&db=pubmed&id=1183067` | Retraction check via **linked relations** | HTTP 200 — no retraction/erratum/correction linkname |
| 8 | `GET .../esummary.fcgi?db=pubmed&id=28035028` + `elink` | Confirm the pre-existing in-repo citation | HTTP 200 — title matches, no retraction relations |

| 9 | `GET .../efetch.fcgi?db=pubmed&id=13632697,1183067,28035028&retmode=xml` **(2026-09-05)** | Retraction check via **typed `CommentsCorrections` relations** | HTTP 200 — no `CommentsCorrections` link of any `RefType` on any record; XML retained with abstracts removed |

An earlier revision of this packet checked retraction status through `pubtype`, then through
`elink`. Neither is a retraction check: an original record retains its own publication type, and
the eLink `pubmed_pubmed*` link sets are related-article neighbourhoods that do not expose the
typed relation. Request 9 is the authority; requests 6–8 are retained for what they are.

A second primary source for the X-heterozygosity *method* claim was searched for and not found
within this change's scope. Rather than map an adjacent paper to it — an earlier revision
mapped a UK Biobank missingness analysis, which did not support the specific claim — that claim
is recorded as pre-existing behaviour and explicitly **not** run through the two-source gate.

No quota limit was reached on the PubMed endpoints; the one quota exhaustion is Scite's.
