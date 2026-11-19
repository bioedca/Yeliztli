# Proposed admission rules for evidence and large files

**Decision requested in #2706.** Retain inspectable, inert extracts of
third-party sources with provenance. Keep first-party code in security
analysis, including code maintained alongside evidence. For #2482, propose
a **2,097,152-byte (2 MiB) limit** for new or growing Git blobs, with narrow,
independently reviewed exceptions. For #2488, prefer the extract convention
over changing the scope of CodeQL. Each implementation has its own issue.

## Tracked inventory

The inventory uses only the public tree at
`7be123f382be44cc3c0936d2787038886b6488f7`. Read the NUL-delimited result of
`git ls-tree -r -l -z` at that commit, retain entries of type `blob` and
measure their reported byte lengths. Count paths separately when two paths
share one object. The percentiles use nearest rank. These bytes describe the
files Git stores before Git's own packing.

| Population | Paths | Distinct blob IDs | Bytes across paths | 99th percentile | Largest |
| --- | ---: | ---: | ---: | ---: | ---: |
| Whole tree | 2,533 | 2,490 | 64,299,033 | 200,704 | 11,374,592 |
| `data/science-evidence/` | 820 | 805 | 7,793,173 | 127,682 | 448,273 |

| Candidate limit | Whole-tree paths above it | Evidence paths above it |
| --- | ---: | ---: |
| 1 MiB | 4 | 0 |
| 2 MiB | 3 | 0 |
| 5 MiB | 3 | 0 |
| 10 MiB | 1 | 0 |

At 1 MiB a 1,100,544-byte annotation CSV also crosses the threshold.
At 2 MiB the only larger paths are an existing SQLite bundle and two
compressed test fixtures. A 5 MiB ceiling admits more future growth
without changing the baseline count. The proposed cap leaves over four
times the largest current evidence file's size. No historical object or
untracked workspace file needs to be rewritten.

## Evidence content and provenance

- Prefer the minimum factual fields that support the claim in UTF-8 text,
  JSON or another documented data format. For a third-party page retain the
  relevant permitted passage or structured fields with the source identifier,
  version, retrieval date, original-byte SHA-256, extract SHA-256, license or
  reuse limits, and the extraction/selection method. Map the claim to that
  extract. Preserve existing scientific citation and independent-source rules.
- When a license, access restriction or incomplete extract prevents later
  verification, record that limitation. Withhold any claim that depends on
  unavailable evidence. Private data stays outside public artifacts.
- The evidence tree contains five HTML files and 28 Python files, including
  an analysis script under a `raw/` directory. A directory name or extension
  cannot certify ownership or inertness. Retain existing history. Inventory
  legacy pages separately and replace a page with a reviewed extract when
  touched, preserving its source and digest. Repository-authored scripts
  keep their normal code review and applicable security analysis.
- Keep evidence out of application assets and rendering/execution paths.
  At the baseline, the wheel lists `backend`, the Dockerfile copies the
  backend and frontend, and the SPA mount serves `frontend/dist`.
  Verify the published artifact and actual served paths if packaging changes.
  For a rare need to preserve full original bytes, require a rights, privacy
  and non-execution review with controlled storage. Record a retrievable
  source and digest in the packet. A size exception grants no scanning or
  publication exception.

Excluding a class of retained files from analysis would require proving
its contents stay inert across packaging, imports and rendering. The
tracked Python files show why a blanket evidence-directory exclusion
would lose coverage. An inert extract also costs less to review and can
retain only the material licensed for reuse.

## Size decisions on a PR

Compare the prospective PR head with its current target merge base using
Git object IDs and byte lengths. Re-evaluate after synchronization. Identify
renames by exact content when applying the unchanged-rename exception.
Compare full blobs even for shallow or incomplete checkouts. If either side
cannot be read, the check cannot pass.

| Change | Proposed result |
| --- | --- |
| Added or grown to at most 2,097,152 bytes | Pass the size check, including exact equality. |
| New path or growth above 2,097,152 bytes | Require an approved exception for that exact path and content. A copy whose source remains is a new path. |
| Existing oversized file unchanged or deleted | Pass. No retrospective allowlist entry or history rewrite. |
| One-to-one rename of identical existing content | Pass the size check if the old path disappears and the destination retains the same purpose and admission controls. Treat an oversized addition to a newly shipped or served surface as a new path. |
| Existing oversized file changes at the same path and stays the same size or shrinks | Pass the size check. Verify provenance, privacy, content and applicable release integrity for the replacement, including when byte counts are equal. |
| Archive or compressed data | Apply the same cap to the stored blob. Document type, source, expected members and expanded size for any proposed archive exception. Expansion requires separate bounded review. |

A size exception names one path, content SHA-256, exact or maximum bytes,
purpose, upstream version/license, responsible maintainer and UTC expiry.
Approve it independently under the load-bearing route and bind it to a
trusted base policy before admitting the blob. The content PR cannot
authorize its own exception. Reject wildcard paths, increased limits,
replacement hashes and expired entries unless independently approved
through that policy. Existing bundles can continue unchanged. A larger
replacement needs its own decision. Report aggregate PR growth for review.

## Acceptance for the two follow-ups

For #2482, use synthetic Git trees to exercise cap minus one, cap, cap plus
one, growth across the cap, a grandfathered bundle, deletion, pure rename,
copy, a changed equal-size oversized blob and a compressed file. Verify
both a valid exact exception and rejection for the wrong path, digest,
size or date, including an attempted self-approval in the same PR.
The required CI result must correspond to the submitted head and base.

For #2488, verify a permitted extract can be independently traced from
source digest to retained fields. Verify package contents and serving
boundaries using harmless synthetic artifacts. Check that an ordinary
first-party JavaScript module and a repository-authored evidence utility
appear in the corresponding language analysis. A green scan alone cannot
demonstrate file coverage. Review any genuine security finding privately.
