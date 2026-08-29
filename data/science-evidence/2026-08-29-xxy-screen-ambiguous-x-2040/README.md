# Withholding the XXY screen's clean negative for an unresolvable X dosage (#2040)

## What this change claims

It **withholds** an affirmative negative. It asserts no new positive finding, introduces no
new threshold, and moves no existing one — the only code change to a constant is making the
already-calibrated hemizygous bound visible to a second module.

Two background facts are load-bearing for the decision logic and are evidenced here. Everything
else in the diff is software behaviour, verifiable from the code and its tests.

## Citations

- `PMID:39806878` / `DOI:10.1111/cen.15200` — Blackburn J, et al. "Klinefelter Syndrome: A Review." *Clin Endocrinol (Oxf)* 2025 (accessed 2026-08-29)
- `PMID:39932051` / `DOI:10.1210/endrev/bnaf005` — Lucas-Herald AK, et al. "New Horizons in Klinefelter Syndrome." *Endocr Rev* 2025 (accessed 2026-08-29)
- `PMID:28035028` / `DOI:10.1093/bioinformatics/btw696` — Qian DC, et al. "seXY: a tool for sex inference from genotype arrays." *Bioinformatics* 2017 (accessed 2026-08-29)
- `PMID:38073250` / `DOI:10.1093/hmg/ddad201` — Chen DZ, et al. "Comprehensive whole-genome analyses of the UK Biobank reveal significant sex differences in both genotype missingness and allele frequency on the X chromosome." *Hum Mol Genet* 2024 (accessed 2026-08-29)

Every record was resolved by PMID through PubMed eSummary, title-matched, and checked for a
retraction or correction `pubtype`; none carries one.

## Claim mapping

| ID | Claim | Evidence | Where it is load-bearing |
| --- | --- | --- | --- |
| C1 | The 47,XXY (Klinefelter) karyotype comprises a supernumerary X **on a Y-bearing male karyotype** — that is, the pattern requires a Y. | Blackburn 2025: *"caused by a supernumerary X chromosome, resulting in a 47 XXY karyotype"*. Lucas-Herald 2025: *"presence of a supernumerary X chromosome (conferring the classical 47,XXY karyotype)"*. | Why an evaluable chrY at or below the noise floor keeps the screen a clean negative however the X reads, and why the escalation is `ambiguous_x and y_discordant` rather than unconditional. |
| C2 | Non-PAR chrX heterozygosity **rate** separates one X from two X on a genotype array, with a wide gap between the two clusters. | Qian 2017 (seXY) thresholds sex inference on the X-het rate rather than a binary count. Chen 2024 independently characterises X-chromosome genotype behaviour differing by sex across UK Biobank. | Why a rate between the calibrated bounds is *unresolved* rather than negative — the premise of routing the middle band to manual review. |

## What is deliberately **not** claimed

The issue's evidence gate discusses mosaic 46,XY/47,XXY and the relationship between X-het and
aneuploid-cell fraction. **None of that is asserted by this change and none of it is evidenced
here**, because the change does not need it: an unresolved X dosage is withheld, not
interpreted. Earlier revisions of the pull request did carry such wording — an
intermediate-chromosome-complement mechanism, a FISH confirmation recommendation, and a
statement about what X heterozygosity measures — and all three were removed rather than
evidenced, for the same reason.

The user-facing copy therefore states only the outcome: the X dosage falls between the levels
the screen can tell apart and could not be determined, this is not a clean negative, and it is
equally not a positive finding.

## Source independence

Each claim has two agreeing sources with disjoint author lists, different journals and
different study types. C1's two are independent clinical reviews sharing no author. C2's are a
method paper (seXY, developed on GERA/PLCO data) and an independent UK Biobank analysis — no
shared author and no shared cohort.

## Discovery-tool ladder

- **Consensus** — invoked 2026-08-29; 20 papers returned, retained as a derived record. Supplied both C1 sources.
- **Scite** — **unavailable**: the monthly MCP call quota was exhausted on 2026-08-27 with a service-reported reset of 2026-09-03, which has not passed. No Scite result was used.
- **Fallback** — the PubMed specialist connector was used in Scite's place, and every retained record was re-resolved through PubMed eSummary for title, journal, DOI and retraction status.

## Retained evidence

`raw/` holds three unedited NCBI PubMed eSummary responses and one derived, sanitized record of
the Consensus invocation — labelled derived rather than a wire capture, because Consensus
answers through MCP and has no HTTP payload to keep. `source-manifest.json` records each exact
request, source, SHA-256 and byte length, together with the citation list, the independence
note and the ladder. No sanitization was required: every payload is a public bibliographic
record containing no genotype, sample, personal or credentialed data.
