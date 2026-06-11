# Expansion Second-Wave — Remaining Implementation Inventory

> **Generated:** 2026-06-10. **Last updated:** 2026-06-11 (bioedca fork) after the SW-A11,
> SW-F1, SW-E1, SW-A12, SW-E6 + warfarin-layer merges.
> **Source of truth:** the 42-PR second-wave plan (Waves A–F).
> **External-input plan:** verified licensing/fetch tier per dataset — referenced below as
> *[ext-strategy]*.
> This file is a **status snapshot**: what is NOT yet implemented, what each item needs,
> and what is tractable now vs blocked on an external dataset/runtime.
>
> **Owner decisions in force (interviewed 2026-06-11):** (1) Licensing = **(A) explicit
> non-commercial** — bundle only CC0/CC-BY (with attribution); non-commercial sources stay
> user-fetch. (2) **PCA = fix, then build on it** (gates B2/B4/B5). (3) Scope = **tractable set
> first** (A11→F1, E1→E2/E6, A12, B1→B3-B8, F3); **Wave C imputation, Wave D HLA/HIBAG, and
> SW-F2 SpliceAI are separately-scheduled (deferred)** — see §6/§12. (4) AlphaMissense bundled
> as **CC-BY-4.0** (authoritative Zenodo 10813168 grant; stale NC-SA file header documented).

---

## 1. Headline status

| Wave | PRs | Done | Partial | Remaining |
|------|-----|------|---------|-----------|
| **A** — cross-cutting rigor + greenfield directly-typed | 12 | 12 | — | 0 |
| **B** — PGS Catalog at scale | 8 | 0 | — | 8 (deferred: needs PCA fix + PGS fetch) |
| **C** — Imputation foundation | 7 | 0 | — | 7 (**separately-scheduled**) |
| **D** — HLA / HIBAG | 6 | 0 | — | 6 (**separately-scheduled**) |
| **E** — Pharmacogenomics expansion | 6 | 5 (E1/E3/E4/E5/E6) | — | 1 (E2) |
| **F** — Deeper variant interpretation | 3 | 1 (F1) | — | 2 (F3; F2 **separately-scheduled**) |
| **Total** | **42** | **18** | — | **~24** (14 separately-scheduled — Wave C: 7, Wave D: 6, SW-F2: 1) |

**Done & merged this session (bioedca fork, 2026-06-11):**
- **SW-A11** (full) — ClinGen gene-disease-validity guardrail + manifest `license`/NOTICE scaffolding.
- **SW-F1** — InterVar-style DRAFT ACMG/AMP engine (unblocked by the A11 ClinGen half).
- **SW-E1** — PharmVar-canonical star-allele defs + panel expansion (NUDT15, UGT1A1) + indeterminate flags; **SW-E1b** follow-on added NAT2 + CYP2B6.
- **SW-A12** — AlphaMissense missense-class REVEL-complement (CC-BY-4.0, Zenodo 10813168).
- **SW-E6** — G6PD (X-linked, sex-aware) + BCHE (succinylcholine apnea) deficiency context.
- **Warfarin layer** (SW-E1 sub-concern) — VKORC1 + CYP4F2 dose-effect context.

**Done previously:** rest of Wave A; Wave E PGx trio — SW-E5 DPYD, SW-E3 CYP2D6 CNV, SW-E4
medication-safety report; patient-safety strand fix (not a plan PR).

**Bottom line:** **~24 PRs remain, of which 14 are separately-scheduled** — Wave C imputation
(×7, §4), Wave D HLA/HIBAG (×6, §5), and SW-F2 SpliceAI (×1, §7). Of the **active** remainder
(~10): **SW-E2** is the last fully-autonomous item; **SW-B1→B8** need a PGS Catalog fetch + the
PCA fix; **SW-F3** needs a GTEx fetch. See §6 for the tractability split.

---

## 2. Wave A — ✅ COMPLETE (12/12)

| PR | # | Goal | Status |
|----|---|------|--------|
| **SW-A11** | 14 | Array-confidence + ClinGen gene-disease-validity guardrail | ✅ **Done** — Weedon-PPV reliability badge (#371) + ClinGen 6-tier validity guardrail (`backend/analysis/gene_validity.py`, `GET /api/analysis/gene-validity`, CC0 snapshot). Unblocked SW-F1. |
| **SW-A12** | 31 | AlphaMissense proteome-wide missense class (additive REVEL complement, **not** a 3rd vote; thresholds 0.34/0.564) | ✅ **Done** (#49) — standalone `alphamissense.db` ingestion + REVEL-complement badge; CC-BY-4.0, Zenodo 10813168 (MD5-pinned); NOTICE documents the stale NC-SA file header. |

---

## 3. Wave B — PGS Catalog at scale (8 remaining)

`SW-B1` unlocks B3–B8. `SW-B2` is independent (no new data).

| PR | # | Goal | Depends on | Needs |
|----|---|------|------------|-------|
| **SW-B1** | 6 | Ingest PGS Catalog GRCh37-harmonized scoring files into SQLite; per-score license honoring; reject build-mismatched scores | — | **PGS Catalog `*_hmPOS_GRCh37.txt.gz` files**, per-score license gating (bundle only permissive; user-fetch NC) — *[ext-strategy]* §PGS |
| **SW-B2** | 5 | Ancestry-continuous PRS calibration (PC1–PC8 → adjusted mean/variance); **fixes calibration not accuracy** (mandatory quantitative mismatch warning) | reuses existing PCA bundle | **No new external data** — tractable now (L effort) |
| **SW-B3** | 46, 45 | Per-PGS provenance/evidence-tier UI + APOE/monogenic exclusion from disease PRS | SW-B1 | (via B1) |
| **SW-B4** | 33 | Prefer multi-ancestry / PRS-CSx scores; select per inferred ancestry | SW-B1 | (via B1) |
| **SW-B5** | 28 | T2D & obesity PRS + anchor SNPs; report % coverage; ancestry-mismatch warning | SW-B1, SW-B2, (SW-C* coverage) | (via B1) |
| **SW-B6** | 56 | Dedicated FH view: APOB R3527Q (rs5742904) + LDL-C polygenic score; frame vs Simon Broome / Dutch Lipid | SW-B1 (LDL-C score) | (via B1) |
| **SW-B7** | 52 | Osteoporosis eBMD PRS — **not** a FRAX/DXA substitute | SW-B1 | (via B1) |
| **SW-B8** | 44 | Opt-in absolute-risk overlay (breast via BOADICEA/CanRisk; SEER/CI5 incidence); **Alembic schema change** | SW-B1 | (via B1) + **schema migration** |

---

## 4. Wave C — Imputation foundation (7 remaining) — ⏸ SEPARATELY-SCHEDULED

> **Deferred by owner decision (2026-06-11):** all of Wave C is parked as a separately-scheduled
> track — it needs a ~40 GB 1000G panel download + a Beagle imputation runtime + laptop-runtime
> measurement, which is its own setup effort outside the current tractable set. Resume when the
> imputation runtime is provisioned.

The biggest unlock, all **L effort with external runtime**. `SW-C1` is the foundation.

| PR | # | Goal | Depends on | Needs |
|----|---|------|------------|-------|
| **SW-C1** | 1 | Ship 1000G imputation reference panel (bref3) via manifest/bundles | — | **1000G panel.** *[ext-strategy]* recommends **1000G Phase 3 v5a** (native b37, pre-built bref3) over NYGC 30× (GRCh38-only → risky phased-panel liftover). ~40 GB. |
| **SW-C2** | 2 | Local Beagle 5.x phase+impute, per-variant DR2/r² persisted (reuse vendored Beagle JAR); **measure laptop runtime** | SW-C1 | Runtime + the panel |
| **SW-C3** | 3 | Hard MAF/r² firewall — imputed rare (MAF<1%) quarantined from P/LP/carrier/monogenic | SW-C2 | (via C2) |
| **SW-C4** | 47 | Imputation-feasibility / reachability labels | SW-C1/2/3 | (via C1–3) |
| **SW-C5** | 7 | Honest PRS coverage gating (genotyped-fraction + imputed-r² tier) | SW-C2/3, SW-B1 | (via C2/3 + B1) |
| **SW-C6** | 32 | Imputation-aware AF + GWAS/ClinVar common-variant uplift | SW-C2/3 | (via C2/3) |
| **SW-C7** | 53 | Advanced engines (GLIMPSE/IMPUTE5 — verify redistribution licenses) + per-sample reach report | SW-C1 | License check + tooling |

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

## 6. Wave E — Pharmacogenomics expansion (1 remaining: E2)

E1/E3/E4/E5/E6 are **done**. Only **SW-E2** remains.

| PR | # | Goal | Status |
|----|---|------|--------|
| **SW-E1** | 15 | PharmVar-canonical versioned star-allele defs; panel expansion; explicit **indeterminate** flags | ✅ **Done** (#34) + **E1b** (#38, NAT2 + CYP2B6). VKORC1/CYP4F2 shipped as the separate **warfarin dose-effect layer** (`backend/analysis/warfarin.py`, #57). |
| **SW-E6** | 35, 22 | G6PD (X-linked, het-female variability) + BCHE + NUDT15 | ✅ **Done** — BCHE succinylcholine apnea (#61) + G6PD sex-aware deficiency (#63); NUDT15 already added in SW-E1. |
| **SW-E2** | 16 | Layer **DPWG + PharmGKB LOE (1A–4) + FDA PGx table** over CPIC (PharmGKB **CC-BY-SA** — honor share-alike) | **Remaining.** Extend `cpic_guidelines` with dpwg/loe/fda columns; surface via the `gene_caveat` seam. **Needs authoritative PharmGKB/DPWG/FDA source data** to avoid guessing clinical values (all 20 current CPIC pairs are PharmGKB LOE 1A by definition; per-pair DPWG/FDA membership needs the real tables). Owner decision pending: fetch the tables vs. ship the verifiable LOE=1A + links subset. |

> Reuse seam already in place from E3/E5: `_GENE_INTERPRETATION_CAVEATS` map →
> `detail_json["gene_caveat"]` → pharma route → `MetabolizerCard`/`MedicationSafetyReport`.

---

## 7. Wave F — Deeper variant interpretation (F1 done; F3 active; F2 deferred)

Coordinate tightly with the validation/Phase-F effort.

| PR | # | Goal | Status |
|----|---|------|--------|
| **SW-F1** | 13 | InterVar-style **DRAFT** ACMG/AMP engine (computable criteria; PVS1 via Abou-Tayoun tree; Tavtigian points). DRAFT/non-clinical, never auto-upgrades a P; PM3 unknown from unphased array | ✅ **Done** — `backend/analysis/acmg.py`, `GET /api/analysis/acmg` (additive, never mutates evidence_level/clinvar_significance). Unblocked by the SW-A11 ClinGen half. |
| **SW-F2** | 38 | SpliceAI precomputed delta-scores (0.2/0.5/0.8) for typed SNPs in splice windows | ⏸ **SEPARATELY-SCHEDULED (deferred 2026-06-11).** Illumina **non-commercial** + BaseSpace-login-gated → never bundle/auto-download (BYO-only). Consistent with the (A) non-commercial posture; revisit when a BYO-ingest path is built. |
| **SW-F3** | 39 | GTEx v8/v10 eQTL/sQTL regulatory layer for typed non-coding SNPs (eQTL = association, not mechanism; do **not** inflate ACMG) | **Active-remaining.** Needs **GTEx open-access `signif_pairs`** fetch (redistribute OK) + **GRCh38 → GRCh37 liftover/rsID match** — *[ext-strategy]* §GTEx. |

---

## 8. Tractability split — current state (post-2026-06-11)

**✅ Done this session (no longer remaining):** SW-A11 (full), SW-A12, SW-E1(+E1b), SW-E6,
SW-F1, and the warfarin VKORC1/CYP4F2 layer. All merged with green post-merge CI.

**Fully autonomous, can start now (no new dataset, no PCA dependency):**
- **SW-E2** — DPWG/PharmGKB-LOE/FDA layer over CPIC. *Caveat:* accurate per-pair DPWG/FDA
  values need the authoritative tables; PharmGKB LOE=1A is a clean rule for all current CPIC
  pairs. **Owner steer requested** (fetch tables vs. verifiable subset) — see §6, §9.

**Blocked on a dataset fetch (owner approves the fetch):**
- **SW-B1** — PGS Catalog GRCh37 hmPOS (per-score license gating). Unlocks B3–B8.
- **SW-F3** — GTEx open-access summaries (+ GRCh38→37 liftover).

**Blocked on the PCA fix (owner-flagged "not working"; tests pass → real-data issue, needs
failure-mode detail):**
- **SW-B2** (PC-continuous calibration), **SW-B4**, **SW-B5** — all consume PCA ancestry.

**Separately-scheduled (deferred by owner decision):**
- **Wave C** (imputation, ×7) — ~40 GB 1000G panel + Beagle runtime.
- **Wave D** (HLA/HIBAG, ×6) — R/Bioconductor subprocess + user-fetched models.
- **SW-F2** (SpliceAI, ×1) — BYO-only; non-commercial + login-gated.

**Gated only by a prerequisite PR (no new data of their own):** SW-B3–B8 (need B1).

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

## 12. Recommended next sequence (as of 2026-06-11)

Steps 1–2 of the original sequence are **done** (A11, A12, E1/E1b, E6, F1, warfarin layer). What remains:

1. **SW-E2** (DPWG/PharmGKB-LOE/FDA over CPIC) — once the owner steers data sourcing (§9.6). Last fully-autonomous item; completes Wave E.
2. **Fix PCA** (§9.5) — needs the owner's observed failure mode; gates SW-B2/B4/B5.
3. **SW-B1** (PGS Catalog fetch) → then **SW-B2** (after PCA) → **SW-B3–B8**. Unlocks the whole Wave-B line.
4. **SW-F3** (GTEx fetch + liftover).
5. **Separately-scheduled:** Wave C (1000G → Beagle), Wave D (HIBAG/R), SW-F2 (SpliceAI BYO) — each resumes as its own track when its runtime/fetch is provisioned.
