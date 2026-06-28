# Expansion Second-Wave — Remaining Implementation Inventory

> **Generated:** 2026-06-10. **Last updated:** 2026-06-28 (bioedca fork) — the entire
> **tractable** second-wave set is merged (SW-A11, SW-F1, SW-E1(+E1b), SW-A12, SW-E6 +
> warfarin layer, all of Wave B, **SW-E2** #1055, **SW-F3** #1053/#1056, **SW-F2**
> #1083/#1088), **and Wave C is now all-but-one merged**: **SW-C1** (#1096, panel fetch),
> **SW-C2** (#1100, Beagle phase+impute runtime), **SW-C3** (#1110, MAF/r² firewall), input-prep
> glue (#1112), persist-DR2 (#1128), **SW-C5** (#1169/#1173, imputation-aware PRS scoring —
> percentiles un-withhold on combined typed+imputed coverage), **SW-C6** (#1183, imputed common
> ClinVar P/LP variants as a firewall-gated finding source), and **SW-C4** (#1184, per-sample
> imputation reachability report). Only **SW-C7** (tooling-gated) and Wave D remain — see §4/§5/§12.
> **Source of truth:** the 42-PR second-wave plan (Waves A–F).
> **External-input plan:** verified licensing/fetch tier per dataset — referenced below as
> *[ext-strategy]*.
> This file is a **status snapshot**: what is NOT yet implemented, what each item needs,
> and what is tractable now vs blocked on an external dataset/runtime.
>
> **Owner decisions in force (interviewed 2026-06-11):** (1) Licensing = **(A) explicit
> non-commercial** — bundle only CC0/CC-BY (with attribution); non-commercial sources stay
> user-fetch. (2) **PCA = fix, then build on it** (gates B2/B4/B5). (3) Scope = **tractable set
> first** (A11→F1, E1→E2/E6, A12, B1→B3-B8, F3); **Wave C imputation and Wave D HLA/HIBAG are
> separately-scheduled (deferred)** — see §4/§5/§12. SW-F2 SpliceAI, originally deferred, is now
> **done** as a BYO-ingest path (#1083/#1088 — its block was bundling/auto-download, not the
> ingest seam, which is pure code consistent with posture (A)). (4) AlphaMissense bundled
> as **CC-BY-4.0** (authoritative Zenodo 10813168 grant; stale NC-SA file header documented).

---

## 1. Headline status

| Wave | PRs | Done | Partial | Remaining |
|------|-----|------|---------|-----------|
| **A** — cross-cutting rigor + greenfield directly-typed | 12 | 12 | — | 0 |
| **B** — PGS Catalog at scale | 8 | 8 | — | 0 ✅ **COMPLETE** |
| **C** — Imputation foundation | 7 | 6 (C1/C2/C3/C4/C5/C6) | — | 1 (C7, tooling-gated) |
| **D** — HLA / HIBAG | 6 | 0 | — | 6 (**owner-gated**) |
| **E** — Pharmacogenomics expansion | 6 | 6 | — | 0 ✅ |
| **F** — Deeper variant interpretation | 3 | 3 (F1/F2/F3) | — | 0 ✅ |
| **Total** | **42** | **35** | — | **7 (Wave C: 1 tooling-gated, Wave D: 6 owner-gated)** |

> **Wave C foundation + SW-C5 merged (bioedca fork, 2026-06-27).** Shipped: **SW-C1** (#1096)
> fetches/verifies the 1000G Phase 3 v5a bref3 panel; **SW-C2** (#1100) runs local Beagle
> phase+impute and parses per-variant DR2; **SW-C3** (#1110) is the hard MAF/r² firewall
> (`backend/analysis/imputation_firewall.py`) quarantining imputed rare (MAF<1%) / low-DR2
> variants from P/LP/carrier/monogenic calls; the input-prep glue (#1112) turns a sample's
> `annotated_variants` into per-chromosome GRCh37 VCFs; **persist-DR2** (#1128,
> `imputation_persist.py` + `scripts/impute_sample.py`) imputes a sample end-to-end and stores
> firewall-cleared imputed variants; and **SW-C5** (#1169 dosage pipeline + #1173 PRS scoring)
> makes PRSs score from firewall-cleared imputed dosages, **un-withholding percentiles** on
> combined typed+imputed coverage (the Wave B promise); **SW-C6** (#1183) makes those
> firewall-cleared imputed common variants a *finding source* at ClinVar P/LP loci the chip did
> not type (labeled imputed-not-typed, firewall re-asserted at `finding_gate`); and **SW-C4**
> (#1184) reports per-sample imputation reachability (panel coverage + backbone density +
> realized-imputed count). **Validated on real data** — a real chr22 sample imputed against the
> shipped panel via SLURM; the shipped parse/firewall/score path handled 430k real records.
> *Learning: the "tail" is mostly pure-code with graceful degradation (empty `imputed_variants` →
> typed-only / no imputed findings), not runtime-blocked.* Remaining: only **SW-C7**
> (GLIMPSE/IMPUTE5, tooling-gated — needs the external engines). See §4 / §8 / §12.

**Wave B completed (bioedca fork, 2026-06-11)** — all 8 merged + the score bundle + frontends:
- **SW-B1/B2** (#100/#116, earlier) — PGS Catalog GRCh37 ingestion + ancestry-continuous calibration.
- **SW-B3** (#123) — per-PGS provenance/evidence-tier + monogenic-exclusion disclosure (APOE gate-safe).
- **SW-B4** (#126) — `pgs_scores.db`→engine bridge, positional matching, ancestry-aware score selection.
- **SW-B5** (#130) — T2D & obesity PRS + anchor SNPs (TCF7L2/FTO/MC4R) + honest coverage gating.
- **SW-B6** (#134) — FH view: monogenic LDLR/APOB/PCSK9 + APOB R3527Q (rs5742904) + LDL-C PRS,
  framed vs DLCN/Simon Broome.
- **SW-B7** (#137) — heel-eBMD gSOS PRS (BYO, non-commercial; explicitly not a DXA/FRAX substitute).
- **SW-B8** (#139) — opt-in breast absolute-risk overlay + Alembic migration 012.
- **Bundle** (#141) — `pgs_scores.db` shipped (CC-BY scores T2D PGS000713, BMI PGS005198, LDL-C
  PGS000688; release `pgs-scores-v1.0.0`). **Frontends** (#142) — Metabolic/FH/eBMD views + B8 opt-in.

**Done earlier (bioedca fork):** SW-A11, SW-F1, SW-E1(+E1b), SW-A12, SW-E6, warfarin layer;
**SW-E2** (#1055) DPWG/PharmGKB-LOE/FDA over CPIC; **SW-F3** (#1053/#1056) GTEx eQTL regulatory
layer; **SW-F2** (#1083/#1088) BYO SpliceAI splice-prediction layer + variant-detail badge.

**Done previously:** rest of Wave A; Wave E PGx trio (E5 DPYD, E3 CYP2D6 CNV, E4 med-safety report).

**Bottom line:** **Waves A, B, E, and F are complete, and Wave C is all-but-one merged**
(SW-C1 #1096, SW-C2 #1100, SW-C3 #1110, input-prep glue #1112, persist-DR2 #1128, SW-C5
imputation-aware PRS scoring #1169/#1173, **SW-C6** #1183 imputed-ClinVar finding source +
firewall-at-gate, and **SW-C4** #1184 reachability report). The remaining work is **7 PRs** —
**SW-C7** (§4) and Wave D HLA/HIBAG (×6, §5), **both infrastructure-first**: SW-C7 needs the
external GLIMPSE/IMPUTE5 engines and **Wave D** needs an R/Bioconductor subprocess + user-fetched
HIBAG models (not obtainable here), so both are parked pending provisioning (and, for Wave D, an
owner decision).

> **Wave-B coverage caveat:** the disease PRSs are genome-wide; on un-imputed array data only
> ~35–57% of each score's variants are typed, so percentiles are *withheld* (coverage reported)
> until Wave C imputation lands. Anchor SNPs + monogenic findings carry the interpretable signal.

---

## 2. Wave A — ✅ COMPLETE (12/12)

| PR | # | Goal | Status |
|----|---|------|--------|
| **SW-A11** | 14 | Array-confidence + ClinGen gene-disease-validity guardrail | ✅ **Done** — Weedon-PPV reliability badge (#371) + ClinGen 6-tier validity guardrail (`backend/analysis/gene_validity.py`, `GET /api/analysis/gene-validity`, CC0 snapshot). Unblocked SW-F1. |
| **SW-A12** | 31 | AlphaMissense proteome-wide missense class (additive REVEL complement, **not** a 3rd vote; thresholds 0.34/0.564) | ✅ **Done** (#49) — standalone `alphamissense.db` ingestion + REVEL-complement badge; CC-BY-4.0, Zenodo 10813168 (MD5-pinned); NOTICE documents the stale NC-SA file header. |

---

## 3. Wave B — PGS Catalog at scale — ✅ COMPLETE (8/8)

All merged on the bioedca fork (2026-06-11). Score bundle `pgs-scores-v1.0.0` ships three CC-BY
GRCh37-harmonized scores (T2D PGS000713, multi-ancestry BMI PGS005198, LDL-C PGS000688); eBMD
(gSOS PGS000657, non-commercial) and breast remain user-fetch / overlay-on-existing per posture (A).

| PR | # | Goal | Status |
|----|---|------|--------|
| **SW-B1** | 6 | Ingest PGS Catalog GRCh37-harmonized scoring files; per-score license honoring | ✅ **Done** (#100) — standalone `pgs_scores.db`; build firewall + license gating + empty-parse guard |
| **SW-B2** | 5 | Ancestry-continuous PRS calibration (fixes calibration not accuracy) | ✅ **Done** (#116) — `continuous_reference_distribution` (HWE mean/var over admixture fractions) |
| **SW-B3** | 46, 45 | Per-PGS provenance/evidence-tier + monogenic exclusion | ✅ **Done** (#123) — provenance fields + `annotate_monogenic_exclusion` (APOE gate-safe) + `PRSProvenance` UI |
| **SW-B4** | 33 | Prefer multi-ancestry / PRS-CSx scores; select per inferred ancestry | ✅ **Done** (#126) — `pgs_bridge` (registry + selection) + positional matching for rsID-less scores |
| **SW-B5** | 28 | T2D & obesity PRS + anchor SNPs; coverage; ancestry-mismatch | ✅ **Done** (#130) — route-only `metabolic` module; coverage-honest (percentile withheld <50%); TCF7L2/FTO/MC4R anchors |
| **SW-B6** | 56 | FH view: APOB R3527Q (rs5742904) + LDL-C PRS; vs Simon Broome / Dutch Lipid | ✅ **Done** (#134) — `fh` module composing monogenic + FDB + LDL-C PRS + DLCN/Simon Broome framing |
| **SW-B7** | 52 | Osteoporosis eBMD PRS — **not** a FRAX/DXA substitute | ✅ **Done** (#137) — `ebmd` module; BYO gSOS (PGS000657, NC); refines-FRAX-not-replaces framing |
| **SW-B8** | 44 | Opt-in absolute-risk overlay (breast; SEER incidence); **Alembic change** | ✅ **Done** (#139) — opt-in consent (Alembic 012) + SEER baseline + BRCA penetrance + CanRisk handoff |
| **Bundle + UI** | — | Ship `pgs_scores.db` + Wave-B frontends | ✅ **Done** (#141 bundle wiring + release; #142 Metabolic/FH/eBMD views + B8 opt-in panel) |

---

## 4. Wave C — Imputation foundation (C1–C6 MERGED; only C7 remains, tooling-gated)

> **C1–C6 merged (2026-06-27/28); only SW-C7 remains.** SW-C1/C2/C3, the input-prep glue,
> persist-DR2, SW-C5 (imputation-aware PRS scoring), **SW-C6** (imputed common ClinVar P/LP
> variants as a firewall-gated finding source), and **SW-C4** (per-sample reachability report)
> all ship. **Learning:** these did **not** require the runtime provisioned locally — they are
> pure code with **graceful degradation** (empty `imputed_variants` → typed-only / no imputed
> findings, byte-identical to the prior path), tested with Beagle mocked, and **validated against
> a real chr22 SLURM run** for output-shape correctness. Only **SW-C7** remains — it needs the
> external GLIMPSE/IMPUTE5 engines (tooling-gated), not just imputed data.

`SW-C1` is the foundation; the firewall (`SW-C3`) is the safety gate the **uplift** track
(`SW-C6`, now merged) sits behind now that imputed variants are a *source* of findings.

| PR | # | Goal | Depends on | Status / Needs |
|----|---|------|------------|----------------|
| **SW-C1** | 1 | Ship 1000G imputation reference panel (bref3) via manifest/bundles | — | ✅ **Done** (#1096) — 1000G Phase 3 v5a (native b37 bref3), SHA-256-pinned manifest, opt-in `scripts/fetch_imputation_panel.py` (~8.5 GB, not auto-fetched). |
| **SW-C2** | 2 | Local Beagle 5.x phase+impute, per-variant DR2/r² parsed (reuse vendored Beagle JAR); **measure laptop runtime** | SW-C1 | ✅ **Done** (#1100) — `imputation_runner.py` (subprocess, GPL-isolated; DR2/AF/IMP/DS parse verified vs a real Beagle 5.5 run) + `scripts/run_imputation.py`. *Only the per-laptop runtime measurement still needs the user's hardware.* |
| **SW-C3** | 3 | Hard MAF/r² firewall — imputed rare (MAF<1%) quarantined from P/LP/carrier/monogenic | SW-C2 | ✅ **Done** (#1110) — `imputation_firewall.py` policy (`assess_variant`; DR2≥0.8 **and** MAF≥1%); evidence-verified thresholds. *Now enforced at the finding gate via `finding_gate.imputed_variant_surfaceable` (SW-C6 #1183).* |
| **(glue)** | — | Sample DB `annotated_variants` → per-chromosome GRCh37 input VCFs the runtime consumes | SW-C1/2 | ✅ **Done** (#1112) — `imputation_input.py` + `scripts/prepare_imputation_input.py` (reference-aligned biallelic SNPs, autosomes 1-22; X deferred). |
| **(persist)** | — | Impute a sample end-to-end + persist firewall-cleared imputed variants (chrom/pos/ref/alt/dr2/af/**dosage**) to the sample DB | SW-C2/3 | ✅ **Done** (#1128 persist + driver, #1169 dosage column) — `imputation_persist.py` (`persist_imputed_variants`, `impute_and_persist_sample`) + `scripts/impute_sample.py`; `imputed_variants` table (schema v16). |
| **SW-C4** | 47 | Imputation-feasibility / reachability labels | SW-C1/2/3 | ✅ **Done** (#1184) — `imputation_reachability.py` (`panel_covers`, `summarize_sample_reachability`) + `GET /api/imputation/reachability`: structural panel coverage (on/off-panel chromosomes) + descriptive backbone density (per-chrom typed-loci count + median gap) + realized-imputed count. Factual report, no invented threshold; graceful. Per-locus LD-aware reachability vs the actual panel sites is a later cluster-validated refinement. |
| **SW-C5** | 7 | Honest PRS coverage gating (genotyped-fraction + imputed-r² tier) | SW-C2/3, SW-B1 | ✅ **Done** (#1169 dosage pipeline / #1173 PRS scoring) — PRSs score from firewall-cleared imputed dosages (`imputed_effect_dosage`), `snps_used_imputed` + `coverage_tier` un-withhold percentiles on combined coverage; graceful typed-only when no imputation persisted. Surfaced in metabolic/eBMD/FH responses. |
| **SW-C6** | 32 | Imputation-aware AF + GWAS/ClinVar common-variant uplift | SW-C2/3 | ✅ **Done** (#1183) — `imputed_findings.py` makes firewall-cleared imputed common variants a finding source at **ClinVar** P/LP loci the chip did not type (labeled imputed-not-typed, dosage→carriage, exact-allele match, evidence capped at 2, confirm-clinically caveat) + **enforces the SW-C3 firewall** at `finding_gate.imputed_variant_surfaceable`. Graceful (no imputation → no findings). **GWAS-association uplift deferred** — GWAS is not yet a finding source even for typed variants (separate concern). |
| **SW-C7** | 53 | Advanced engines (GLIMPSE/IMPUTE5 — verify redistribution licenses) + per-sample reach report | SW-C1 | ⏳ **Tooling-gated** — GLIMPSE=MIT (bundleable), IMPUTE5=academic-only (BYO); needs the external tools. |

---

## 5. Wave D — HLA / HIBAG (6 remaining) — ⏸ SEPARATELY-SCHEDULED

> **Deferred by owner decision (2026-06-11):** the whole HLA/HIBAG track is parked — it needs an
> R/Bioconductor subprocess design (GPL isolation) plus user-fetched classifier models that are
> never bundleable. Resume as its own track after the R-subprocess seam exists.

Needs an **R subprocess** (GPL-isolated) + **user-fetched classifier models** (no-license / proprietary-derived → never bundle — *[ext-strategy]* §HIBAG). `SW-D1` is the foundation.

| PR | # | Goal | Depends on |
|----|---|------|------------|
| **SW-D1** | 17 | Core HIBAG engine (R subprocess; ancestry/locus-gated posteriors; African/admixed capped to 2-field). Supersedes the single-tag HLA proxy (keep proxy fallback) | — |
| **SW-D2** | 18 | HLA drug-hypersensitivity (B*57:01, B*15:02, A*31:01, B*58:01, B*13:01) — imputed, confirm-with-clinical-HLA banner | SW-D1 |
| **SW-D3** | 19 | Celiac (DQ2.5/DQ8) + narcolepsy (DQB1*06:02) high-NPV rule-OUT reports | SW-D1 |
| **SW-D4** | 36, 42 | Autoimmune susceptibility (B*27, DRB1 shared epitope, C*06:02, T1D DR-DQ) + celiac/RA card | SW-D1 |
| **SW-D5** | 37 | Raw imputed-HLA viewer/export (NPV framing; never transplant/donor match) | SW-D1 |
| **SW-D6** | 54 | DEEP*HLA upgrade path — **low priority, defer** (licensing hard) | SW-D1 |

---

## 6. Wave E — Pharmacogenomics expansion — ✅ COMPLETE (6/6)

All six are **done** (SW-E2 was the last).

| PR | # | Goal | Status |
|----|---|------|--------|
| **SW-E1** | 15 | PharmVar-canonical versioned star-allele defs; panel expansion; explicit **indeterminate** flags | ✅ **Done** (#34) + **E1b** (#38, NAT2 + CYP2B6). VKORC1/CYP4F2 shipped as the separate **warfarin dose-effect layer** (`backend/analysis/warfarin.py`, #57). |
| **SW-E6** | 35, 22 | G6PD (X-linked, het-female variability) + BCHE + NUDT15 | ✅ **Done** — BCHE succinylcholine apnea (#61) + G6PD sex-aware deficiency (#63); NUDT15 already added in SW-E1. |
| **SW-E2** | 16 | Layer **DPWG + PharmGKB LOE (1A–4) + FDA PGx table** over CPIC (PharmGKB **CC-BY-SA** — honor share-alike) | ✅ **Done** (#1055) — cross-source PGx evidence strip (`backend/analysis/pgx_guidelines.py` + `backend/data/pgx/pgx_guideline_sources.csv`) surfaced via the `gene_caveat` seam. |

> Reuse seam already in place from E3/E5: `_GENE_INTERPRETATION_CAVEATS` map →
> `detail_json["gene_caveat"]` → pharma route → `MetabolizerCard`/`MedicationSafetyReport`.

---

## 7. Wave F — Deeper variant interpretation — ✅ COMPLETE (3/3)

Coordinate tightly with the validation/Phase-F effort.

| PR | # | Goal | Status |
|----|---|------|--------|
| **SW-F1** | 13 | InterVar-style **DRAFT** ACMG/AMP engine (computable criteria; PVS1 via Abou-Tayoun tree; Tavtigian points). DRAFT/non-clinical, never auto-upgrades a P; PM3 unknown from unphased array | ✅ **Done** — `backend/analysis/acmg.py`, `GET /api/analysis/acmg` (additive, never mutates evidence_level/clinvar_significance). Unblocked by the SW-A11 ClinGen half. |
| **SW-F2** | 38 | SpliceAI precomputed delta-scores (0.2/0.5/0.8) for typed SNPs in splice windows | ✅ **Done** (#1083 backend / #1088 frontend) — **BYO-ingest path**: `backend/annotation/spliceai.py` (position-keyed GRCh37 ingest, min-DS floor, empty-parse guard) + `backend/analysis/spliceai.py` context badge (tiers at 0.2/0.5/0.8; `acmg_evidence=False`) + `scripts/ingest_spliceai_scores.py` + variant-detail `SpliceAIBadge`. Illumina **non-commercial** + BaseSpace-login-gated → never bundled/auto-downloaded (registered `build_mode="manual"`); the user supplies the hg19 VCF. Thresholds + score definition evidence-verified (PMID:30661751 / DOI:10.1016/j.cell.2018.12.015). |
| **SW-F3** | 39 | GTEx v8/v10 eQTL/sQTL regulatory layer for typed non-coding SNPs (eQTL = association, not mechanism; do **not** inflate ACMG) | ✅ **Done** (#1053 backend / #1056 frontend) — `backend/annotation/gtex_eqtl.py` (GRCh38 `variant_id`→dbSNP rsID match, no liftover) + `backend/analysis/gtex.py` context badge (`acmg_evidence=False`) + variant-detail `GTExEqtlBadge`. Pipeline-built standalone `gtex_eqtl.db`. |

---

## 8. Tractability split — current state (2026-06-28)

**✅ Done (no longer remaining):** all of Wave A; all of Wave B (+ the `pgs_scores.db` bundle +
frontends); all of Wave E (incl. SW-E2 #1055); all of Wave F (SW-F1, SW-F3 #1053/#1056, SW-F2
PRs #1083/#1088); **and all of Wave C except SW-C7** — SW-C1 #1096, SW-C2 #1100, SW-C3 #1110, the
input-prep glue #1112, persist-DR2 #1128, the imputed-dosage pipeline #1169, **SW-C5** #1173
(imputation-aware PRS scoring), **SW-C6** #1183 (imputed common ClinVar P/LP variants as a
firewall-gated finding source), and **SW-C4** #1184 (per-sample reachability report). All merged
with green CI; `main` stays releasable. (The earlier PCA-fix gate on SW-B2/B4/B5 was resolved as
those landed — Wave B is COMPLETE.)

**Learning (revised 2026-06-28):** the earlier "runtime-gated" framing of the Wave C tail was
**too pessimistic.** SW-C5, persist-DR2, SW-C6, and SW-C4 all shipped as **pure code with graceful
degradation** (empty `imputed_variants` → typed-only / no imputed findings, byte-identical to the
prior path), unit-tested with Beagle mocked, and **validated for output-shape correctness against
a real chr22 SLURM run**. Only SW-C7 needs the external GLIMPSE/IMPUTE5 tools provisioned.

**Remaining work:**
- **Wave C** (×1): **SW-C7** (GLIMPSE/IMPUTE5 advanced engines — **tooling-gated**: needs the
  external engines provisioned, not just imputed data). *(SW-C6's GWAS-association uplift was
  deferred as a separate concern — GWAS is not yet a finding source even for typed variants.)*
- **Wave D** (HLA/HIBAG, ×6) — needs an **R/Bioconductor subprocess** (GPL-isolated) running the
  HIBAG classifier, plus **user-fetched classifier models** (no-license / proprietary-derived →
  never bundleable, and not obtainable here). SW-D1 (the R-subprocess engine) is the foundation
  and can keep the existing single-tag HLA proxy as a fallback.

Wave D still does not reduce to a pure-code feature (it needs its external runtime/dataset
provisioned **first**), so it resumes as its own scheduled
track (§4/§5/§12).

---

## 9. Cross-cutting decisions — RESOLVED + still-open

**Resolved (interviewed 2026-06-11):**
1. **Commercial-use posture → (A) explicit non-commercial.** Bundle only CC0/CC-BY (with
   attribution); non-commercial sources (dbNSFP academic branch, SpliceAI) stay BYO/user-fetch,
   never redistributed. Keeps SpliceAI's NC clause consistent.
2. **Manifest `license` field → DONE.** Added in SW-A11/A12; `bundles/manifest.json` now carries
   per-source `license` fields + pipeline pins, with a `NOTICE` attribution file (ClinGen CC0,
   AlphaMissense CC-BY-4.0).
3. **AlphaMissense licensing → CC-BY-4.0** (authoritative Zenodo 10813168 grant; the stale
   `# Licensed under CC BY-NC-SA 4.0` file header is documented in NOTICE + the module docstring).

**Still open (owner-gated):**
4. **GRCh37 liftover burden.** 1000G NYGC 30×, GTEx v8/v10, dbNSFP are GRCh38-native. Plan: use
   **1000G Phase 3 v5a** (native b37 + pre-built bref3) for Wave C v1; liftover/rsID-match GTEx;
   dbNSFP handled by the F35 cross-build guard.
5. **PCA "not working" failure mode.** Owner flagged PCA ancestry inference as broken, but all
   184 ancestry tests pass and the bundle is real (5000 AIMs × 8 PCs, 3419 ref samples) — so it
   is a **real-data/accuracy** issue, not a crash. Needs the owner's observed symptom (wrong
   ancestry on real samples? a specific population? strand handling?) before SW-B2/B4/B5 can build
   on it. Candidate cause: `_encode_dosage` strand handling vs. the AIM a1/a2 alleles.
6. **SW-E2 data sourcing.** Fetch the authoritative PharmGKB/DPWG/FDA tables (PharmGKB CC-BY-SA →
   share-alike) vs. ship only the verifiable PharmGKB-LOE=1A + guideline-link subset.

---

## 10. Known tech-debt / follow-ups (not plan PRs)

- **4 wrong-strand CPIC indel rows** — flagged `KNOWN_NON_SNV`, inert (array data unreliable for indels); clean up if PharmVar (SW-E1) supersedes them.
- **`sample_23andme_v5.txt` legacy-strand CYP2D6 genotypes** — test-fixture strand artifact noted during the #382 strand fix.
- **SW-A4 provenance — NEEDS-VERIFY in real runs:** all findings stamped by `stamp_findings_provenance` share one release snapshot, so per-finding release-deltas only differ across runs (not within a run). Re-confirm once external sources (AlphaMissense/PGS/GTEx/ClinGen) start flowing distinct `database_versions`.

---

## 11. Out of scope (plan §8 — do NOT duplicate here)

- Anything the **validation / Phase-F** effort owns — evidence-tier / in-silico / carriage / rarity logic (open Phase-F items: none load-bearing for second-wave now that F30 landed).
- The **Yeliztli rebrand** residual manual phases (worktree/folder rename, live config migration) — separate, owner-gated.
- Net-new proposals beyond `EXPANSION_STRATEGY.md` §11.

---

## 12. Recommended next sequence (as of 2026-06-28)

Waves A, B, E, F are complete, and **Wave C is all-but-one merged** — SW-C1 #1096, SW-C2 #1100,
SW-C3 #1110, input-prep #1112, persist-DR2 #1128, dosage #1169, **SW-C5** #1173 (PRS scoring),
**SW-C6** #1183 (imputed ClinVar finding source + firewall at the gate), and **SW-C4** #1184
(reachability report). What remains is **2 blocked tracks**:

1. **Wave C — SW-C7 only (tooling-gated).** GLIMPSE (MIT, bundleable) / IMPUTE5 (academic-only
   BYO) advanced engines + per-sample reach report. Needs the external engines **provisioned
   first** (not just imputed data), so it is not a pure-code PR today — mirror the Beagle
   `imputation_runner.py` subprocess seam once a GLIMPSE binary is available, and validate the
   output shape on a cluster run. *(SW-C6's GWAS-association uplift is a separate, still-open
   concern: GWAS is not yet a finding source even for typed variants.)*
2. **Wave D — HLA/HIBAG (×6).** Stand up an R/Bioconductor subprocess (GPL-isolated) + a BYO path
   for the user-fetched HIBAG classifier models (never bundleable, not obtainable here), then
   SW-D1 (core engine, proxy fallback retained) → SW-D2–D6. Needs the R-subprocess seam + an
   owner go-ahead.

Both remaining tracks are **infrastructure-first** — neither reduces to a clean, green, atomic
pure-code PR until its external runtime/dataset is provisioned (GLIMPSE/IMPUTE5 for SW-C7; the
R/Bioconductor runtime + user-fetched HIBAG models for Wave D), so both stay parked pending that
provisioning and (for Wave D) an owner decision.
